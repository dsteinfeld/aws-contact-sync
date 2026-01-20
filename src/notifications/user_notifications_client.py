"""AWS User Notifications client wrapper with fallback to SNS."""

import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Literal, Any
from datetime import datetime
import boto3
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)


@dataclass
class NotificationConfig:
    """Configuration for notification delivery."""
    notification_hub_region: str
    delivery_channels: List[str]
    notification_rules: Dict[str, List[str]]
    fallback_sns_topic: str
    notify_on_failure: bool = True
    notify_on_success: bool = False
    notify_on_partial_failure: bool = True
    failure_threshold: int = 1

    def __post_init__(self):
        """Validate configuration."""
        if not self.notification_hub_region.strip():
            raise ValueError("notification_hub_region cannot be empty")
        if not self.delivery_channels:
            raise ValueError("delivery_channels cannot be empty")
        if not self.fallback_sns_topic.strip():
            raise ValueError("fallback_sns_topic cannot be empty")
        if self.failure_threshold < 0:
            raise ValueError("failure_threshold cannot be negative")


@dataclass
class NotificationMessage:
    """Structured notification message."""
    title: str
    message: str
    priority: Literal["high", "medium", "low"]
    notification_type: str
    metadata: Dict[str, Any]
    timestamp: datetime

    def __post_init__(self):
        """Validate message."""
        if not self.title.strip():
            raise ValueError("title cannot be empty")
        if not self.message.strip():
            raise ValueError("message cannot be empty")
        if self.priority not in ["high", "medium", "low"]:
            raise ValueError(f"Invalid priority: {self.priority}")


class UserNotificationsClient:
    """AWS User Notifications client with SNS fallback."""

    def __init__(self, config: NotificationConfig, region: str = None):
        """Initialize the client.
        
        Args:
            config: Notification configuration
            region: AWS region (defaults to config.notification_hub_region)
        """
        self.config = config
        self.region = region or config.notification_hub_region
        
        # Initialize AWS clients
        self._user_notifications_client = None
        self._sns_client = None
        self._account_mgmt_client = None
        self._security_contact_email = None
        
    def _get_user_notifications_client(self):
        """Get or create User Notifications client."""
        if self._user_notifications_client is None:
            self._user_notifications_client = boto3.client(
                'notifications',
                region_name=self.config.notification_hub_region
            )
        return self._user_notifications_client
    
    def _get_sns_client(self):
        """Get or create SNS client."""
        if self._sns_client is None:
            self._sns_client = boto3.client('sns', region_name=self.region)
        return self._sns_client
    
    def _get_account_mgmt_client(self):
        """Get or create Account Management client."""
        if self._account_mgmt_client is None:
            self._account_mgmt_client = boto3.client('account', region_name=self.region)
        return self._account_mgmt_client
    
    def _get_security_contact_email(self) -> Optional[str]:
        """Get the Security alternate contact email from management account.
        
        Returns:
            Security contact email address or None if not found
        """
        if self._security_contact_email is not None:
            return self._security_contact_email
        
        try:
            client = self._get_account_mgmt_client()
            response = client.get_alternate_contact(AlternateContactType='SECURITY')
            self._security_contact_email = response['AlternateContact']['EmailAddress']
            logger.info(f"Retrieved Security contact email: {self._security_contact_email}")
            return self._security_contact_email
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == 'ResourceNotFoundException':
                logger.warning("No Security alternate contact configured in management account")
            else:
                logger.error(f"Failed to retrieve Security contact: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error retrieving Security contact: {e}")
            return None

    def send_notification(self, message: NotificationMessage) -> bool:
        """Send notification using User Notifications with SNS fallback.
        
        Args:
            message: Notification message to send
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        user_notifications_error = None
        
        # Try User Notifications first
        try:
            if self._send_user_notification(message):
                logger.info(f"Notification sent via User Notifications: {message.title}")
                return True
        except Exception as e:
            user_notifications_error = str(e)
            logger.warning(f"User Notifications failed: {user_notifications_error}")
        
        # Fallback to SNS with error details
        if self._send_sns_notification(message, user_notifications_error):
            logger.info(f"Notification sent via SNS fallback: {message.title}")
            return True
        
        logger.error(f"Failed to send notification via both User Notifications and SNS: {message.title}")
        return False

    def _send_user_notification(self, message: NotificationMessage) -> bool:
        """Send notification via AWS User Notifications to Security contact.
        
        Args:
            message: Notification message to send
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Get Security contact email
            security_email = self._get_security_contact_email()
            if not security_email:
                logger.warning("No Security contact email available, cannot send User Notification")
                return False
            
            client = self._get_user_notifications_client()
            
            # Create notification content
            content = {
                'title': message.title,
                'body': message.message,
                'metadata': {
                    **message.metadata,
                    'timestamp': message.timestamp.isoformat(),
                    'notification_type': message.notification_type,
                    'priority': message.priority
                }
            }
            
            # Send notification via User Notifications
            # Using the correct API for AWS User Notifications service
            response = client.send_user_notification(
                NotificationConfiguration={
                    'Subject': message.title,
                    'Body': message.message,
                    'Priority': message.priority.upper()
                },
                Recipients=[{
                    'Type': 'EMAIL',
                    'Address': security_email
                }],
                Source='aws-contact-sync',
                EventType=message.notification_type
            )
            
            logger.info(f"User Notification sent to Security contact: {security_email}")
            return response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200
            
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.warning(f"User Notifications API error ({error_code}): {error_msg}")
            raise Exception(f"User Notifications API error: {error_code} - {error_msg}")
        except BotoCoreError as e:
            logger.warning(f"User Notifications connection error: {e}")
            raise Exception(f"User Notifications connection error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in User Notifications: {e}")
            raise

    def _send_sns_notification(self, message: NotificationMessage, user_notifications_error: Optional[str] = None) -> bool:
        """Send notification via SNS fallback.
        
        Args:
            message: Notification message to send
            user_notifications_error: Error from User Notifications attempt (if any)
            
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._get_sns_client()
            
            # Add User Notifications error to message if present
            message_body = message.message
            if user_notifications_error:
                message_body = f"""
NOTE: This notification was sent via SNS fallback because AWS User Notifications failed.
User Notifications Error: {user_notifications_error}

---

{message.message}
                """.strip()
            
            # Create SNS message
            sns_message = {
                'default': message_body,
                'email': self._format_email_message(message, user_notifications_error)
            }
            
            # Send to SNS topic
            response = client.publish(
                TopicArn=self.config.fallback_sns_topic,
                Message=json.dumps(sns_message),
                MessageStructure='json',
                Subject=f"[SNS Fallback] {message.title}" if user_notifications_error else message.title,
                MessageAttributes={
                    'priority': {
                        'DataType': 'String',
                        'StringValue': message.priority
                    },
                    'notification_type': {
                        'DataType': 'String',
                        'StringValue': message.notification_type
                    },
                    'fallback': {
                        'DataType': 'String',
                        'StringValue': 'true' if user_notifications_error else 'false'
                    }
                }
            )
            
            return response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200
            
        except (ClientError, BotoCoreError) as e:
            logger.error(f"SNS fallback failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in SNS fallback: {e}")
            return False

    def _get_channels_for_priority(self, priority: str) -> List[str]:
        """Get delivery channels for the given priority level.
        
        Args:
            priority: Priority level (high, medium, low)
            
        Returns:
            List of delivery channels
        """
        # For high priority, use all channels
        if priority == "high":
            return self.config.delivery_channels
        # For medium priority, exclude SMS to reduce noise
        elif priority == "medium":
            return [ch for ch in self.config.delivery_channels if ch != "SMS"]
        # For low priority, use only email
        else:
            return [ch for ch in self.config.delivery_channels if ch == "EMAIL"]

    def _format_email_message(self, message: NotificationMessage, user_notifications_error: Optional[str] = None) -> str:
        """Format message for email delivery.
        
        Args:
            message: Notification message
            user_notifications_error: Error from User Notifications attempt (if any)
            
        Returns:
            Formatted email message
        """
        email_body = f"""
{message.title}

{message.message}

Priority: {message.priority.upper()}
Type: {message.notification_type}
Timestamp: {message.timestamp.isoformat()}

Metadata:
{json.dumps(message.metadata, indent=2)}
        """.strip()
        
        if user_notifications_error:
            email_body = f"""
================================================================================
NOTIFICATION DELIVERY NOTICE
================================================================================

This notification was sent via SNS fallback because AWS User Notifications 
failed to deliver the message.

User Notifications Error:
{user_notifications_error}

================================================================================

{email_body}
            """.strip()
        
        email_body += "\n\n---\nAWS Contact Synchronization System"
        return email_body

    def should_notify(self, notification_type: str, failed_accounts: int, total_accounts: int) -> bool:
        """Determine if notification should be sent based on configuration.
        
        Args:
            notification_type: Type of notification
            failed_accounts: Number of failed accounts
            total_accounts: Total number of accounts processed
            
        Returns:
            True if notification should be sent
        """
        if notification_type in ["complete_failure", "permission_errors", "system_errors"]:
            return self.config.notify_on_failure
        elif notification_type == "partial_failure":
            return (self.config.notify_on_partial_failure and 
                   failed_accounts >= self.config.failure_threshold)
        elif notification_type == "success_completion":
            return self.config.notify_on_success
        else:
            return True  # Default to sending notification for unknown types
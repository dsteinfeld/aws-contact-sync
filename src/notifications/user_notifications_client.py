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

    def send_notification(self, message: NotificationMessage) -> bool:
        """Send notification using User Notifications with SNS fallback.
        
        Args:
            message: Notification message to send
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        # Try User Notifications first
        if self._send_user_notification(message):
            logger.info(f"Notification sent via User Notifications: {message.title}")
            return True
        
        # Fallback to SNS
        if self._send_sns_notification(message):
            logger.info(f"Notification sent via SNS fallback: {message.title}")
            return True
        
        logger.error(f"Failed to send notification: {message.title}")
        return False

    def _send_user_notification(self, message: NotificationMessage) -> bool:
        """Send notification via AWS User Notifications.
        
        Args:
            message: Notification message to send
            
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._get_user_notifications_client()
            
            # Determine delivery channels based on priority
            channels = self._get_channels_for_priority(message.priority)
            
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
            
            # Send notification
            response = client.create_event_rule(
                name=f"contact-sync-{message.notification_type}-{int(message.timestamp.timestamp())}",
                eventPattern=json.dumps({
                    'source': ['aws.contact-sync'],
                    'detail-type': [message.notification_type],
                    'detail': content
                }),
                targets=[{
                    'id': '1',
                    'arn': f"arn:aws:notifications:{self.config.notification_hub_region}:*:delivery-channel/{channel}",
                    'input': json.dumps(content)
                } for channel in channels]
            )
            
            return response.get('ResponseMetadata', {}).get('HTTPStatusCode') == 200
            
        except (ClientError, BotoCoreError) as e:
            logger.warning(f"User Notifications failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error in User Notifications: {e}")
            return False

    def _send_sns_notification(self, message: NotificationMessage) -> bool:
        """Send notification via SNS fallback.
        
        Args:
            message: Notification message to send
            
        Returns:
            True if successful, False otherwise
        """
        try:
            client = self._get_sns_client()
            
            # Create SNS message
            sns_message = {
                'default': message.message,
                'email': self._format_email_message(message),
                'sms': self._format_sms_message(message)
            }
            
            # Send to SNS topic
            response = client.publish(
                TopicArn=self.config.fallback_sns_topic,
                Message=json.dumps(sns_message),
                MessageStructure='json',
                Subject=message.title,
                MessageAttributes={
                    'priority': {
                        'DataType': 'String',
                        'StringValue': message.priority
                    },
                    'notification_type': {
                        'DataType': 'String',
                        'StringValue': message.notification_type
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

    def _format_email_message(self, message: NotificationMessage) -> str:
        """Format message for email delivery.
        
        Args:
            message: Notification message
            
        Returns:
            Formatted email message
        """
        return f"""
{message.title}

{message.message}

Priority: {message.priority.upper()}
Type: {message.notification_type}
Timestamp: {message.timestamp.isoformat()}

Metadata:
{json.dumps(message.metadata, indent=2)}

---
AWS Contact Synchronization System
        """.strip()

    def _format_sms_message(self, message: NotificationMessage) -> str:
        """Format message for SMS delivery.
        
        Args:
            message: Notification message
            
        Returns:
            Formatted SMS message (truncated for SMS limits)
        """
        # SMS messages should be concise
        sms_text = f"AWS Contact Sync: {message.title} - {message.message}"
        # Truncate to SMS limit (160 characters)
        if len(sms_text) > 160:
            sms_text = sms_text[:157] + "..."
        return sms_text

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
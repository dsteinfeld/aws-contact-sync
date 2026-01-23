"""Main notification handler for AWS Contact Synchronization."""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from ..models.sync_models import SyncOperation, AccountSyncResult
from ..config.dynamodb_config_manager import DynamoDBConfigManager
from .user_notifications_client import UserNotificationsClient, NotificationConfig
from .message_formatter import NotificationMessageFormatter

logger = logging.getLogger(__name__)


class NotificationHandler:
    """Handles all notification scenarios for contact synchronization."""

    def __init__(self, config_manager: DynamoDBConfigManager, region: str = "us-east-1"):
        """Initialize the notification handler.
        
        Args:
            config_manager: Configuration manager for retrieving notification settings
            region: AWS region for SNS fallback
        """
        self.config_manager = config_manager
        self.region = region
        self._notification_client = None

    def _get_notification_client(self) -> Optional[UserNotificationsClient]:
        """Get or create notification client based on current configuration.
        
        Returns:
            UserNotificationsClient instance or None if configuration is invalid
        """
        try:
            config_obj = self.config_manager.read_config()
            if not config_obj:
                logger.warning("No configuration found")
                return None
            
            config = config_obj.to_dict()
            notification_settings = config.get("notification_settings", {})
            
            if not notification_settings:
                logger.warning("No notification settings found in configuration")
                return None
            
            user_notifications_config = notification_settings.get("user_notifications_config", {})
            
            # Create notification config
            notification_config = NotificationConfig(
                notification_hub_region=user_notifications_config.get("notification_hub_region", "us-east-1"),
                delivery_channels=user_notifications_config.get("delivery_channels", ["EMAIL"]),
                notification_rules=user_notifications_config.get("notification_rules", {}),
                fallback_sns_topic=notification_settings.get("fallback_sns_topic", ""),
                notify_on_failure=notification_settings.get("notify_on_failure", True),
                notify_on_success=notification_settings.get("notify_on_success", False),
                notify_on_partial_failure=notification_settings.get("notify_on_partial_failure", True),
                failure_threshold=notification_settings.get("failure_threshold", 1)
            )
            
            return UserNotificationsClient(notification_config, self.region)
            
        except Exception as e:
            logger.error(f"Failed to create notification client: {e}")
            return None

    def handle_sync_completion(self, sync_operation: SyncOperation) -> bool:
        """Handle notifications for completed synchronization operations.
        
        Args:
            sync_operation: Completed sync operation
            
        Returns:
            True if notifications were sent successfully, False otherwise
        """
        client = self._get_notification_client()
        if not client:
            logger.warning("No notification client available, skipping notifications")
            return False

        try:
            # Analyze sync results
            failed_results = [r for r in sync_operation.results.values() if r.status == "failed"]
            successful_results = [r for r in sync_operation.results.values() if r.status == "success"]
            total_accounts = len(sync_operation.target_accounts)
            
            notification_sent = False
            
            # Determine notification type and send appropriate notification
            if len(failed_results) == total_accounts:
                # Complete failure
                message = NotificationMessageFormatter.format_complete_failure(sync_operation)
                if client.should_notify("complete_failure", len(failed_results), total_accounts):
                    notification_sent = self._send_with_retry(client, message)
                    
            elif len(failed_results) > 0:
                # Partial failure - check for permission errors
                permission_errors = [r for r in failed_results if self._is_permission_error(r)]
                
                if permission_errors:
                    message = NotificationMessageFormatter.format_permission_errors(
                        sync_operation, permission_errors
                    )
                    if client.should_notify("permission_errors", len(failed_results), total_accounts):
                        notification_sent = self._send_with_retry(client, message)
                else:
                    message = NotificationMessageFormatter.format_partial_failure(sync_operation)
                    if client.should_notify("partial_failure", len(failed_results), total_accounts):
                        notification_sent = self._send_with_retry(client, message)
                        
            else:
                # Complete success
                message = NotificationMessageFormatter.format_success_completion(sync_operation)
                if client.should_notify("success_completion", 0, total_accounts):
                    notification_sent = self._send_with_retry(client, message)
            
            # Log notification results
            if notification_sent:
                logger.info(f"Notification sent successfully for sync {sync_operation.sync_id}")
            else:
                logger.warning(f"Failed to send notification for sync {sync_operation.sync_id}")
            
            return notification_sent or True  # Return True if no notification needed
            
        except Exception as e:
            logger.error(f"Failed to handle sync completion notification: {e}")
            # Try to send a system error notification about the notification failure
            self._handle_notification_failure(sync_operation, str(e))
            return False

    def handle_system_error(self, sync_operation: SyncOperation, error_message: str) -> bool:
        """Handle notifications for system-level errors.
        
        Args:
            sync_operation: Sync operation that encountered system error
            error_message: System error message
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        client = self._get_notification_client()
        if not client:
            logger.warning("No notification client available, skipping system error notification")
            return False

        try:
            message = NotificationMessageFormatter.format_system_errors(sync_operation, error_message)
            total_accounts = len(sync_operation.target_accounts)
            
            if client.should_notify("system_errors", total_accounts, total_accounts):
                notification_sent = self._send_with_retry(client, message)
                
                if notification_sent:
                    logger.info(f"System error notification sent for sync {sync_operation.sync_id}")
                else:
                    logger.error(f"Failed to send system error notification for sync {sync_operation.sync_id}")
                
                return notification_sent
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send system error notification: {e}")
            return False

    def handle_configuration_error(self, error_message: str, config_details: Dict[str, Any]) -> bool:
        """Handle notifications for configuration errors.
        
        Args:
            error_message: Configuration error message
            config_details: Configuration details that caused the error
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        client = self._get_notification_client()
        if not client:
            logger.warning("No notification client available, skipping configuration error notification")
            return False

        try:
            message = NotificationMessageFormatter.format_configuration_errors(error_message, config_details)
            
            if client.should_notify("configuration_errors", 1, 1):
                notification_sent = self._send_with_retry(client, message)
                
                if notification_sent:
                    logger.info("Configuration error notification sent successfully")
                else:
                    logger.error("Failed to send configuration error notification")
                
                return notification_sent
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send configuration error notification: {e}")
            return False

    def _is_permission_error(self, result: AccountSyncResult) -> bool:
        """Check if an account sync result represents a permission error.
        
        Args:
            result: Account sync result to check
            
        Returns:
            True if the result represents a permission error
        """
        if not result.error_message:
            return False
        
        # Common permission error indicators
        permission_indicators = [
            "AccessDenied",
            "UnauthorizedOperation",
            "Forbidden",
            "InsufficientPermissions",
            "InvalidUserID.NotFound",
            "AssumeRoleFailure"
        ]
        
        error_msg = result.error_message.lower()
        return any(indicator.lower() in error_msg for indicator in permission_indicators)

    def test_notification_delivery(self) -> Dict[str, bool]:
        """Test notification delivery for all configured channels.
        
        Returns:
            Dictionary with test results for each notification type
        """
        client = self._get_notification_client()
        if not client:
            return {"error": "No notification client available"}

        results = {}
        
        try:
            # Create a test sync operation
            from datetime import datetime
            from ..models.contact_models import ContactInformation
            
            test_contact = ContactInformation(
                address_line1="123 Test St",
                city="Test City",
                country_code="US",
                full_name="Test User",
                phone_number="+1-555-0123",
                postal_code="12345"
            )
            
            test_sync = SyncOperation(
                sync_id="test-notification",
                timestamp=datetime.now(timezone.utc),
                initiating_user="arn:aws:iam::123456789012:user/test-user",
                contact_type="primary",
                source_account="123456789012",
                target_accounts=["234567890123"],
                status="completed",
                contact_data=test_contact,
                results={
                    "234567890123": AccountSyncResult(
                        account_id="234567890123",
                        status="success",
                        timestamp=datetime.now(timezone.utc)
                    )
                }
            )
            
            # Test success notification
            success_message = NotificationMessageFormatter.format_success_completion(test_sync)
            results["success_notification"] = client.send_notification(success_message)
            
            logger.info(f"Notification delivery test results: {results}")
            return results
            
        except Exception as e:
            logger.error(f"Failed to test notification delivery: {e}")
            return {"error": str(e)}

    def _send_with_retry(self, client, message, max_retries: int = 3) -> bool:
        """Send notification with retry logic for delivery failures.
        
        Args:
            client: Notification client
            message: Notification message to send
            max_retries: Maximum number of retry attempts
            
        Returns:
            True if notification was sent successfully, False otherwise
        """
        import time
        
        for attempt in range(max_retries + 1):
            try:
                if client.send_notification(message):
                    if attempt > 0:
                        logger.info(f"Notification sent successfully on attempt {attempt + 1}")
                    return True
                
                if attempt < max_retries:
                    # Exponential backoff: 1s, 2s, 4s
                    delay = 2 ** attempt
                    logger.warning(f"Notification delivery failed, retrying in {delay}s (attempt {attempt + 1}/{max_retries + 1})")
                    time.sleep(delay)
                
            except Exception as e:
                logger.error(f"Error sending notification on attempt {attempt + 1}: {e}")
                if attempt < max_retries:
                    delay = 2 ** attempt
                    time.sleep(delay)
        
        logger.error(f"Failed to send notification after {max_retries + 1} attempts")
        return False

    def _handle_notification_failure(self, sync_operation: SyncOperation, error_message: str):
        """Handle notification delivery failures by logging and attempting fallback.
        
        Args:
            sync_operation: Sync operation that failed to notify
            error_message: Error message describing the notification failure
        """
        try:
            # Log the notification failure
            logger.error(f"Notification system failure for sync {sync_operation.sync_id}: {error_message}")
            
            # Try to send a simplified notification via SNS fallback only
            client = self._get_notification_client()
            if client and client._sns_client:
                fallback_message = f"""
AWS Contact Sync Notification System Failure

Sync ID: {sync_operation.sync_id}
Contact Type: {sync_operation.contact_type}
Source Account: {sync_operation.source_account}
Target Accounts: {len(sync_operation.target_accounts)}

The notification system encountered an error and could not deliver the sync completion notification.
Please check CloudWatch logs for detailed information.

Error: {error_message}
                """.strip()
                
                try:
                    client._get_sns_client().publish(
                        TopicArn=client.config.fallback_sns_topic,
                        Message=fallback_message,
                        Subject="AWS Contact Sync: Notification System Failure"
                    )
                    logger.info("Fallback notification sent via SNS")
                except Exception as sns_error:
                    logger.error(f"Even SNS fallback failed: {sns_error}")
                    
        except Exception as e:
            logger.error(f"Failed to handle notification failure: {e}")

    def get_notification_status(self, sync_id: str) -> Dict[str, Any]:
        """Get notification status for a specific sync operation.
        
        Args:
            sync_id: Sync operation ID
            
        Returns:
            Dictionary with notification status information
        """
        try:
            # This would typically query a notification tracking table
            # For now, return basic status information
            return {
                "sync_id": sync_id,
                "notification_enabled": self._get_notification_client() is not None,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get notification status: {e}")
            return {
                "sync_id": sync_id,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
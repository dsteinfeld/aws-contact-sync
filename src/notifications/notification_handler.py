"""Main notification handler for AWS Contact Synchronization."""

import logging
from typing import List, Dict, Any, Optional
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
            config = self.config_manager.get_configuration()
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
            
            # Determine notification type and send appropriate notification
            if len(failed_results) == total_accounts:
                # Complete failure
                message = NotificationMessageFormatter.format_complete_failure(sync_operation)
                if client.should_notify("complete_failure", len(failed_results), total_accounts):
                    return client.send_notification(message)
                    
            elif len(failed_results) > 0:
                # Partial failure - check for permission errors
                permission_errors = [r for r in failed_results if self._is_permission_error(r)]
                
                if permission_errors:
                    message = NotificationMessageFormatter.format_permission_errors(
                        sync_operation, permission_errors
                    )
                    if client.should_notify("permission_errors", len(failed_results), total_accounts):
                        return client.send_notification(message)
                else:
                    message = NotificationMessageFormatter.format_partial_failure(sync_operation)
                    if client.should_notify("partial_failure", len(failed_results), total_accounts):
                        return client.send_notification(message)
                        
            else:
                # Complete success
                message = NotificationMessageFormatter.format_success_completion(sync_operation)
                if client.should_notify("success_completion", 0, total_accounts):
                    return client.send_notification(message)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to handle sync completion notification: {e}")
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
                return client.send_notification(message)
            
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
                return client.send_notification(message)
            
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
                timestamp=datetime.utcnow(),
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
                        timestamp=datetime.utcnow()
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
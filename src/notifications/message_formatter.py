"""Message formatting for different notification scenarios."""

from datetime import datetime, UTC
from typing import Dict, List, Any
from ..models.sync_models import SyncOperation, AccountSyncResult
from .user_notifications_client import NotificationMessage


class NotificationMessageFormatter:
    """Formats notification messages for different scenarios."""

    @staticmethod
    def format_complete_failure(sync_operation: SyncOperation) -> NotificationMessage:
        """Format notification for complete synchronization failure.
        
        Args:
            sync_operation: The failed sync operation
            
        Returns:
            Formatted notification message
        """
        failed_accounts = [
            result for result in sync_operation.results.values() 
            if result.status == "failed"
        ]
        
        title = f"AWS Contact Sync: Complete Failure - {sync_operation.contact_type}"
        
        message = f"""
Contact synchronization failed for ALL member accounts.

Sync Details:
- Contact Type: {sync_operation.contact_type}
- Source Account: {sync_operation.source_account}
- Initiating User: {sync_operation.initiating_user}
- Total Accounts: {len(sync_operation.target_accounts)}
- Failed Accounts: {len(failed_accounts)}

Failed Accounts:
{NotificationMessageFormatter._format_failed_accounts(failed_accounts)}

Recommended Actions:
1. Check AWS service health status
2. Verify IAM permissions for the sync service
3. Review CloudWatch logs for detailed error information
4. Consider manual contact updates if urgent
        """.strip()
        
        metadata = {
            "sync_id": sync_operation.sync_id,
            "contact_type": sync_operation.contact_type,
            "source_account": sync_operation.source_account,
            "failed_count": len(failed_accounts),
            "total_count": len(sync_operation.target_accounts),
            "failure_rate": 1.0
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="high",
            notification_type="complete_failure",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def format_partial_failure(sync_operation: SyncOperation) -> NotificationMessage:
        """Format notification for partial synchronization failure.
        
        Args:
            sync_operation: The partially failed sync operation
            
        Returns:
            Formatted notification message
        """
        failed_accounts = [
            result for result in sync_operation.results.values() 
            if result.status == "failed"
        ]
        successful_accounts = [
            result for result in sync_operation.results.values() 
            if result.status == "success"
        ]
        
        title = f"AWS Contact Sync: Partial Failure - {sync_operation.contact_type}"
        
        message = f"""
Contact synchronization partially failed.

Sync Details:
- Contact Type: {sync_operation.contact_type}
- Source Account: {sync_operation.source_account}
- Initiating User: {sync_operation.initiating_user}
- Total Accounts: {len(sync_operation.target_accounts)}
- Successful: {len(successful_accounts)}
- Failed: {len(failed_accounts)}

Failed Accounts:
{NotificationMessageFormatter._format_failed_accounts(failed_accounts)}

Recommended Actions:
1. Review failed account permissions
2. Check CloudWatch logs for specific error details
3. Consider retrying failed accounts manually
        """.strip()
        
        failure_rate = len(failed_accounts) / len(sync_operation.target_accounts)
        
        metadata = {
            "sync_id": sync_operation.sync_id,
            "contact_type": sync_operation.contact_type,
            "source_account": sync_operation.source_account,
            "failed_count": len(failed_accounts),
            "successful_count": len(successful_accounts),
            "total_count": len(sync_operation.target_accounts),
            "failure_rate": failure_rate
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="medium",
            notification_type="partial_failure",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def format_success_completion(sync_operation: SyncOperation) -> NotificationMessage:
        """Format notification for successful synchronization.
        
        Args:
            sync_operation: The successful sync operation
            
        Returns:
            Formatted notification message
        """
        successful_accounts = [
            result for result in sync_operation.results.values() 
            if result.status == "success"
        ]
        
        title = f"AWS Contact Sync: Success - {sync_operation.contact_type}"
        
        message = f"""
Contact synchronization completed successfully.

Sync Details:
- Contact Type: {sync_operation.contact_type}
- Source Account: {sync_operation.source_account}
- Initiating User: {sync_operation.initiating_user}
- Accounts Updated: {len(successful_accounts)}
        """.strip()
        
        metadata = {
            "sync_id": sync_operation.sync_id,
            "contact_type": sync_operation.contact_type,
            "source_account": sync_operation.source_account,
            "successful_count": len(successful_accounts),
            "total_count": len(sync_operation.target_accounts),
            "failure_rate": 0.0
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="low",
            notification_type="success_completion",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def format_permission_errors(sync_operation: SyncOperation, permission_errors: List[AccountSyncResult]) -> NotificationMessage:
        """Format notification for permission-related failures.
        
        Args:
            sync_operation: The sync operation with permission errors
            permission_errors: List of accounts with permission errors
            
        Returns:
            Formatted notification message
        """
        title = f"AWS Contact Sync: Permission Errors - {sync_operation.contact_type}"
        
        message = f"""
Contact synchronization failed due to permission errors.

Sync Details:
- Contact Type: {sync_operation.contact_type}
- Source Account: {sync_operation.source_account}
- Initiating User: {sync_operation.initiating_user}
- Accounts with Permission Errors: {len(permission_errors)}

Permission Errors:
{NotificationMessageFormatter._format_failed_accounts(permission_errors)}

Recommended Actions:
1. Verify IAM permissions for the contact sync service
2. Check if member accounts have proper trust relationships
3. Ensure the management account has necessary permissions
4. Review AWS Organizations service control policies
        """.strip()
        
        metadata = {
            "sync_id": sync_operation.sync_id,
            "contact_type": sync_operation.contact_type,
            "source_account": sync_operation.source_account,
            "permission_error_count": len(permission_errors),
            "total_count": len(sync_operation.target_accounts)
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="high",
            notification_type="permission_errors",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def format_system_errors(sync_operation: SyncOperation, error_message: str) -> NotificationMessage:
        """Format notification for system-level errors.
        
        Args:
            sync_operation: The sync operation that encountered system errors
            error_message: System error message
            
        Returns:
            Formatted notification message
        """
        title = f"AWS Contact Sync: System Error - {sync_operation.contact_type}"
        
        message = f"""
Contact synchronization encountered a system error.

Sync Details:
- Contact Type: {sync_operation.contact_type}
- Source Account: {sync_operation.source_account}
- Initiating User: {sync_operation.initiating_user}

Error Details:
{error_message}

Recommended Actions:
1. Check AWS service health dashboard
2. Review CloudWatch logs for detailed error information
3. Verify Lambda function configuration and limits
4. Check DynamoDB table availability and capacity
5. Contact AWS support if the issue persists
        """.strip()
        
        metadata = {
            "sync_id": sync_operation.sync_id,
            "contact_type": sync_operation.contact_type,
            "source_account": sync_operation.source_account,
            "error_message": error_message,
            "total_count": len(sync_operation.target_accounts)
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="high",
            notification_type="system_errors",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def format_configuration_errors(error_message: str, config_details: Dict[str, Any]) -> NotificationMessage:
        """Format notification for configuration-related errors.
        
        Args:
            error_message: Configuration error message
            config_details: Configuration details that caused the error
            
        Returns:
            Formatted notification message
        """
        title = "AWS Contact Sync: Configuration Error"
        
        message = f"""
Contact synchronization failed due to configuration error.

Error Details:
{error_message}

Configuration Details:
{NotificationMessageFormatter._format_config_details(config_details)}

Recommended Actions:
1. Review and correct the configuration settings
2. Validate configuration against the schema
3. Check for typos in account IDs or contact types
4. Ensure all required fields are provided
        """.strip()
        
        metadata = {
            "error_message": error_message,
            "config_details": config_details
        }
        
        return NotificationMessage(
            title=title,
            message=message,
            priority="medium",
            notification_type="configuration_errors",
            metadata=metadata,
            timestamp=datetime.now(UTC)
        )

    @staticmethod
    def _format_failed_accounts(failed_accounts: List[AccountSyncResult]) -> str:
        """Format failed accounts for display in notifications.
        
        Args:
            failed_accounts: List of failed account results
            
        Returns:
            Formatted string of failed accounts
        """
        if not failed_accounts:
            return "None"
        
        lines = []
        for result in failed_accounts[:10]:  # Limit to first 10 to avoid overly long messages
            error_msg = result.error_message or "Unknown error"
            lines.append(f"- {result.account_id}: {error_msg}")
        
        if len(failed_accounts) > 10:
            lines.append(f"... and {len(failed_accounts) - 10} more accounts")
        
        return "\n".join(lines)

    @staticmethod
    def _format_config_details(config_details: Dict[str, Any]) -> str:
        """Format configuration details for display.
        
        Args:
            config_details: Configuration details dictionary
            
        Returns:
            Formatted string of configuration details
        """
        lines = []
        for key, value in config_details.items():
            if isinstance(value, list):
                lines.append(f"- {key}: {', '.join(map(str, value))}")
            else:
                lines.append(f"- {key}: {value}")
        return "\n".join(lines)
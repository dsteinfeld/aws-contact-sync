"""Property-based tests for status reporting completeness.

Feature: aws-contact-sync, Property 8: Status Reporting Completeness
Validates: Requirements 2.5, 3.3
"""

import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings, assume
from typing import Dict, Any, List

from src.notifications.notification_handler import NotificationHandler
from src.notifications.message_formatter import NotificationMessageFormatter
from src.models.contact_models import ContactInformation, AlternateContact
from src.models.sync_models import SyncOperation, AccountSyncResult
from src.config.dynamodb_config_manager import DynamoDBConfigManager


class TestStatusReportingProperties:
    """Property-based tests for status reporting completeness."""
    
    @given(
        num_accounts=st.integers(min_value=1, max_value=20),
        success_ratio=st.floats(min_value=0.0, max_value=1.0),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        has_permission_errors=st.booleans(),
        has_system_errors=st.booleans()
    )
    @settings(max_examples=100, deadline=None)
    def test_status_reporting_completeness(
        self, 
        num_accounts: int, 
        success_ratio: float,
        contact_type: str,
        has_permission_errors: bool,
        has_system_errors: bool
    ):
        """Property 8: For any completed synchronization operation, the system should generate 
        a comprehensive report showing the status (success/failure/skipped) for each target 
        account along with relevant timestamps and error details.
        
        **Validates: Requirements 2.5, 3.3**
        """
        # Generate test data
        sync_id = str(uuid.uuid4())
        management_account = "123456789012"
        initiating_user = f"arn:aws:iam::{management_account}:user/test-user"
        
        # Generate target accounts
        target_accounts = [f"{100000000000 + i:012d}" for i in range(num_accounts)]
        
        # Generate contact data based on type
        if contact_type == "primary":
            contact_data = ContactInformation(
                address_line1="123 Test St",
                city="Test City", 
                country_code="US",
                full_name="Test User",
                phone_number="+1-555-0123",
                postal_code="12345"
            )
        else:
            contact_data = AlternateContact(
                contact_type=contact_type,
                email_address="test@example.com",
                name="Test Contact",
                phone_number="+1-555-0123",
                title="Test Title"
            )
        
        # Generate account results based on success ratio
        num_successful = int(num_accounts * success_ratio)
        num_failed = num_accounts - num_successful
        
        results = {}
        timestamp = datetime.now(timezone.utc)
        
        # Create successful results
        for i in range(num_successful):
            account_id = target_accounts[i]
            results[account_id] = AccountSyncResult(
                account_id=account_id,
                status="success",
                timestamp=timestamp,
                error_message=None,
                retry_count=0
            )
        
        # Create failed results
        for i in range(num_successful, num_accounts):
            account_id = target_accounts[i]
            
            # Determine error type
            if has_permission_errors and i % 2 == 0:
                error_message = "AccessDenied: Insufficient permissions to update contact information"
            elif has_system_errors and i % 3 == 0:
                error_message = "InternalError: Lambda function timeout"
            else:
                error_message = "ServiceUnavailable: Account Management API temporarily unavailable"
            
            results[account_id] = AccountSyncResult(
                account_id=account_id,
                status="failed",
                timestamp=timestamp,
                error_message=error_message,
                retry_count=2 if "ServiceUnavailable" in error_message else 0
            )
        
        # Create sync operation
        sync_operation = SyncOperation(
            sync_id=sync_id,
            timestamp=timestamp,
            initiating_user=initiating_user,
            contact_type=contact_type,
            source_account=management_account,
            target_accounts=target_accounts,
            status="completed",
            contact_data=contact_data,
            results=results
        )
        
        # Test status reporting completeness
        self._verify_status_reporting_completeness(sync_operation)
    
    def _verify_status_reporting_completeness(self, sync_operation: SyncOperation):
        """Verify that status reporting includes all required information."""
        # Mock configuration manager
        mock_config_manager = Mock(spec=DynamoDBConfigManager)
        mock_config_manager.get_configuration.return_value = {
            "notification_settings": {
                "user_notifications_config": {
                    "notification_hub_region": "us-east-1",
                    "delivery_channels": ["EMAIL"],
                    "notification_rules": {
                        "high_priority": ["complete_failure", "permission_errors", "system_errors"],
                        "medium_priority": ["partial_failure", "configuration_errors"],
                        "low_priority": ["success_completion"]
                    }
                },
                "fallback_sns_topic": "arn:aws:sns:us-east-1:123456789012:contact-sync-fallback",
                "notify_on_failure": True,
                "notify_on_success": True,
                "notify_on_partial_failure": True,
                "failure_threshold": 1
            }
        }
        
        # Create notification handler
        notification_handler = NotificationHandler(mock_config_manager)
        
        # Analyze sync results
        failed_results = [r for r in sync_operation.results.values() if r.status == "failed"]
        successful_results = [r for r in sync_operation.results.values() if r.status == "success"]
        total_accounts = len(sync_operation.target_accounts)
        
        # Determine expected notification type
        if len(failed_results) == total_accounts:
            # Complete failure
            message = NotificationMessageFormatter.format_complete_failure(sync_operation)
            expected_type = "complete_failure"
        elif len(failed_results) > 0:
            # Check for permission errors
            permission_errors = [r for r in failed_results if self._is_permission_error(r)]
            if permission_errors:
                message = NotificationMessageFormatter.format_permission_errors(
                    sync_operation, permission_errors
                )
                expected_type = "permission_errors"
            else:
                message = NotificationMessageFormatter.format_partial_failure(sync_operation)
                expected_type = "partial_failure"
        else:
            # Complete success
            message = NotificationMessageFormatter.format_success_completion(sync_operation)
            expected_type = "success_completion"
        
        # Verify message completeness
        self._verify_message_completeness(message, sync_operation, expected_type)
        
        # Verify all accounts are accounted for in the results
        self._verify_account_coverage(sync_operation)
        
        # Verify timestamps and error details are present where expected
        self._verify_result_details(sync_operation)
    
    def _verify_message_completeness(self, message, sync_operation: SyncOperation, expected_type: str):
        """Verify that the notification message contains all required information."""
        # Message should have all required fields
        assert message.title, "Notification message must have a title"
        assert message.message, "Notification message must have a message body"
        assert message.priority in ["high", "medium", "low"], "Message must have valid priority"
        assert message.notification_type == expected_type, f"Expected type {expected_type}, got {message.notification_type}"
        assert message.timestamp, "Message must have a timestamp"
        assert message.metadata, "Message must have metadata"
        
        # Metadata should contain sync operation details
        metadata = message.metadata
        assert metadata.get("sync_id") == sync_operation.sync_id, "Metadata must include sync_id"
        assert metadata.get("contact_type") == sync_operation.contact_type, "Metadata must include contact_type"
        assert metadata.get("source_account") == sync_operation.source_account, "Metadata must include source_account"
        assert metadata.get("total_count") == len(sync_operation.target_accounts), "Metadata must include total_count"
        
        # For failure scenarios, should include failure counts
        failed_count = len([r for r in sync_operation.results.values() if r.status == "failed"])
        if failed_count > 0:
            assert "failed_count" in metadata or "permission_error_count" in metadata, "Metadata must include failure count for failed operations"
        
        # For partial failures, should include success count
        if expected_type == "partial_failure":
            successful_count = len([r for r in sync_operation.results.values() if r.status == "success"])
            assert metadata.get("successful_count") == successful_count, "Metadata must include successful_count for partial failures"
        
        # Should include failure rate for completed operations
        if expected_type in ["complete_failure", "partial_failure", "success_completion"]:
            expected_failure_rate = failed_count / len(sync_operation.target_accounts)
            assert abs(metadata.get("failure_rate", 0) - expected_failure_rate) < 0.001, "Metadata must include accurate failure_rate"
    
    def _verify_account_coverage(self, sync_operation: SyncOperation):
        """Verify that all target accounts are covered in the results."""
        target_account_set = set(sync_operation.target_accounts)
        result_account_set = set(sync_operation.results.keys())
        
        # All target accounts should have results
        assert target_account_set == result_account_set, "All target accounts must have corresponding results"
        
        # All results should have valid statuses
        for account_id, result in sync_operation.results.items():
            assert result.status in ["pending", "success", "failed", "skipped"], f"Account {account_id} has invalid status: {result.status}"
            assert result.account_id == account_id, f"Result account_id {result.account_id} doesn't match key {account_id}"
    
    def _verify_result_details(self, sync_operation: SyncOperation):
        """Verify that result details are complete and consistent."""
        for account_id, result in sync_operation.results.items():
            # All results should have timestamps
            assert result.timestamp, f"Account {account_id} result must have a timestamp"
            
            # Failed results should have error messages
            if result.status == "failed":
                assert result.error_message, f"Failed account {account_id} must have an error message"
                assert isinstance(result.retry_count, int), f"Account {account_id} must have integer retry_count"
                assert result.retry_count >= 0, f"Account {account_id} retry_count cannot be negative"
            
            # Successful results should not have error messages
            if result.status == "success":
                assert result.error_message is None, f"Successful account {account_id} should not have error message"
    
    def _is_permission_error(self, result: AccountSyncResult) -> bool:
        """Check if an account sync result represents a permission error."""
        if not result.error_message:
            return False
        
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


@pytest.mark.property
class TestStatusReportingEdgeCases:
    """Test edge cases for status reporting."""
    
    def test_empty_results_handling(self):
        """Test status reporting with empty results."""
        sync_operation = SyncOperation(
            sync_id="test-empty",
            timestamp=datetime.now(timezone.utc),
            initiating_user="arn:aws:iam::123456789012:user/test",
            contact_type="primary",
            source_account="123456789012",
            target_accounts=["234567890123"],
            status="failed",
            contact_data=ContactInformation(
                address_line1="123 Test St",
                city="Test City",
                country_code="US", 
                full_name="Test User",
                phone_number="+1-555-0123",
                postal_code="12345"
            ),
            results={}  # Empty results
        )
        
        # Should handle empty results gracefully
        message = NotificationMessageFormatter.format_system_errors(
            sync_operation, "System error: No results generated"
        )
        
        assert message.title
        assert message.message
        assert message.notification_type == "system_errors"
        assert message.metadata.get("sync_id") == sync_operation.sync_id
    
    def test_all_skipped_accounts(self):
        """Test status reporting when all accounts are skipped."""
        results = {
            "234567890123": AccountSyncResult(
                account_id="234567890123",
                status="skipped",
                timestamp=datetime.now(timezone.utc),
                error_message="Account excluded by configuration"
            )
        }
        
        sync_operation = SyncOperation(
            sync_id="test-skipped",
            timestamp=datetime.now(timezone.utc),
            initiating_user="arn:aws:iam::123456789012:user/test",
            contact_type="primary",
            source_account="123456789012",
            target_accounts=["234567890123"],
            status="completed",
            contact_data=ContactInformation(
                address_line1="123 Test St",
                city="Test City",
                country_code="US",
                full_name="Test User", 
                phone_number="+1-555-0123",
                postal_code="12345"
            ),
            results=results
        )
        
        # Should treat skipped accounts as successful completion
        message = NotificationMessageFormatter.format_success_completion(sync_operation)
        
        assert message.notification_type == "success_completion"
        assert message.metadata.get("total_count") == 1
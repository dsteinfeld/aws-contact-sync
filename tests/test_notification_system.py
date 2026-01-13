"""Unit tests for notification system components.

Tests notification formatting, delivery, and fallback mechanisms.
Requirements: 2.5, 3.3
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock, call
from botocore.exceptions import ClientError

from src.notifications.user_notifications_client import (
    UserNotificationsClient, NotificationConfig, NotificationMessage
)
from src.notifications.message_formatter import NotificationMessageFormatter
from src.notifications.notification_handler import NotificationHandler
from src.models.contact_models import ContactInformation, AlternateContact
from src.models.sync_models import SyncOperation, AccountSyncResult
from src.config.dynamodb_config_manager import DynamoDBConfigManager


class TestNotificationConfig:
    """Test notification configuration validation."""
    
    def test_valid_config_creation(self):
        """Test creating valid notification configuration."""
        config = NotificationConfig(
            notification_hub_region="us-east-1",
            delivery_channels=["EMAIL", "SMS"],
            notification_rules={
                "high_priority": ["complete_failure"],
                "medium_priority": ["partial_failure"],
                "low_priority": ["success_completion"]
            },
            fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback"
        )
        
        assert config.notification_hub_region == "us-east-1"
        assert config.delivery_channels == ["EMAIL", "SMS"]
        assert config.notify_on_failure is True  # Default value
        assert config.failure_threshold == 1  # Default value
    
    def test_invalid_config_empty_region(self):
        """Test configuration validation with empty region."""
        with pytest.raises(ValueError, match="notification_hub_region cannot be empty"):
            NotificationConfig(
                notification_hub_region="",
                delivery_channels=["EMAIL"],
                notification_rules={},
                fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback"
            )
    
    def test_invalid_config_empty_channels(self):
        """Test configuration validation with empty delivery channels."""
        with pytest.raises(ValueError, match="delivery_channels cannot be empty"):
            NotificationConfig(
                notification_hub_region="us-east-1",
                delivery_channels=[],
                notification_rules={},
                fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback"
            )
    
    def test_invalid_config_negative_threshold(self):
        """Test configuration validation with negative failure threshold."""
        with pytest.raises(ValueError, match="failure_threshold cannot be negative"):
            NotificationConfig(
                notification_hub_region="us-east-1",
                delivery_channels=["EMAIL"],
                notification_rules={},
                fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback",
                failure_threshold=-1
            )


class TestNotificationMessage:
    """Test notification message validation."""
    
    def test_valid_message_creation(self):
        """Test creating valid notification message."""
        message = NotificationMessage(
            title="Test Notification",
            message="This is a test message",
            priority="high",
            notification_type="test",
            metadata={"key": "value"},
            timestamp=datetime.now(timezone.utc)
        )
        
        assert message.title == "Test Notification"
        assert message.priority == "high"
        assert message.notification_type == "test"
    
    def test_invalid_message_empty_title(self):
        """Test message validation with empty title."""
        with pytest.raises(ValueError, match="title cannot be empty"):
            NotificationMessage(
                title="",
                message="Test message",
                priority="high",
                notification_type="test",
                metadata={},
                timestamp=datetime.now(timezone.utc)
            )
    
    def test_invalid_message_priority(self):
        """Test message validation with invalid priority."""
        with pytest.raises(ValueError, match="Invalid priority"):
            NotificationMessage(
                title="Test",
                message="Test message",
                priority="invalid",
                notification_type="test",
                metadata={},
                timestamp=datetime.now(timezone.utc)
            )


class TestUserNotificationsClient:
    """Test AWS User Notifications client functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.config = NotificationConfig(
            notification_hub_region="us-east-1",
            delivery_channels=["EMAIL", "SMS", "SLACK"],
            notification_rules={
                "high_priority": ["complete_failure", "permission_errors"],
                "medium_priority": ["partial_failure"],
                "low_priority": ["success_completion"]
            },
            fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback"
        )
        self.client = UserNotificationsClient(self.config)
    
    def test_channel_selection_high_priority(self):
        """Test delivery channel selection for high priority messages."""
        channels = self.client._get_channels_for_priority("high")
        assert channels == ["EMAIL", "SMS", "SLACK"]
    
    def test_channel_selection_medium_priority(self):
        """Test delivery channel selection for medium priority messages."""
        channels = self.client._get_channels_for_priority("medium")
        assert channels == ["EMAIL", "SLACK"]  # Excludes SMS
    
    def test_channel_selection_low_priority(self):
        """Test delivery channel selection for low priority messages."""
        channels = self.client._get_channels_for_priority("low")
        assert channels == ["EMAIL"]  # Only email
    
    def test_should_notify_complete_failure(self):
        """Test notification decision for complete failure."""
        should_notify = self.client.should_notify("complete_failure", 5, 5)
        assert should_notify is True
    
    def test_should_notify_partial_failure_above_threshold(self):
        """Test notification decision for partial failure above threshold."""
        should_notify = self.client.should_notify("partial_failure", 2, 5)
        assert should_notify is True
    
    def test_should_notify_partial_failure_below_threshold(self):
        """Test notification decision for partial failure below threshold."""
        config = NotificationConfig(
            notification_hub_region="us-east-1",
            delivery_channels=["EMAIL"],
            notification_rules={},
            fallback_sns_topic="arn:aws:sns:us-east-1:123456789012:fallback",
            failure_threshold=3
        )
        client = UserNotificationsClient(config)
        
        should_notify = client.should_notify("partial_failure", 1, 5)
        assert should_notify is False
    
    def test_should_notify_success_disabled(self):
        """Test notification decision for success when disabled."""
        should_notify = self.client.should_notify("success_completion", 0, 5)
        assert should_notify is False  # Default is disabled
    
    @patch('boto3.client')
    def test_send_notification_user_notifications_success(self, mock_boto_client):
        """Test successful notification via User Notifications."""
        # Mock User Notifications client
        mock_notifications_client = Mock()
        mock_notifications_client.create_event_rule.return_value = {
            'ResponseMetadata': {'HTTPStatusCode': 200}
        }
        mock_boto_client.return_value = mock_notifications_client
        
        message = NotificationMessage(
            title="Test Notification",
            message="Test message",
            priority="high",
            notification_type="test",
            metadata={"key": "value"},
            timestamp=datetime.now(timezone.utc)
        )
        
        result = self.client.send_notification(message)
        assert result is True
        mock_notifications_client.create_event_rule.assert_called_once()
    
    @patch('boto3.client')
    def test_send_notification_fallback_to_sns(self, mock_boto_client):
        """Test fallback to SNS when User Notifications fails."""
        # Mock User Notifications client to fail
        mock_notifications_client = Mock()
        mock_notifications_client.create_event_rule.side_effect = ClientError(
            {'Error': {'Code': 'ServiceUnavailable'}}, 'CreateEventRule'
        )
        
        # Mock SNS client to succeed
        mock_sns_client = Mock()
        mock_sns_client.publish.return_value = {
            'ResponseMetadata': {'HTTPStatusCode': 200}
        }
        
        def mock_client_factory(service_name, **kwargs):
            if service_name == 'notifications':
                return mock_notifications_client
            elif service_name == 'sns':
                return mock_sns_client
            return Mock()
        
        mock_boto_client.side_effect = mock_client_factory
        
        message = NotificationMessage(
            title="Test Notification",
            message="Test message",
            priority="high",
            notification_type="test",
            metadata={"key": "value"},
            timestamp=datetime.now(timezone.utc)
        )
        
        result = self.client.send_notification(message)
        assert result is True
        mock_sns_client.publish.assert_called_once()
    
    @patch('boto3.client')
    def test_send_notification_both_fail(self, mock_boto_client):
        """Test notification failure when both User Notifications and SNS fail."""
        # Mock both clients to fail
        mock_client = Mock()
        mock_client.create_event_rule.side_effect = ClientError(
            {'Error': {'Code': 'ServiceUnavailable'}}, 'CreateEventRule'
        )
        mock_client.publish.side_effect = ClientError(
            {'Error': {'Code': 'ServiceUnavailable'}}, 'Publish'
        )
        mock_boto_client.return_value = mock_client
        
        message = NotificationMessage(
            title="Test Notification",
            message="Test message",
            priority="high",
            notification_type="test",
            metadata={"key": "value"},
            timestamp=datetime.now(timezone.utc)
        )
        
        result = self.client.send_notification(message)
        assert result is False
    
    def test_format_email_message(self):
        """Test email message formatting."""
        message = NotificationMessage(
            title="Test Notification",
            message="Test message body",
            priority="high",
            notification_type="test",
            metadata={"sync_id": "123", "account": "456"},
            timestamp=datetime.now(timezone.utc)
        )
        
        formatted = self.client._format_email_message(message)
        
        assert "Test Notification" in formatted
        assert "Test message body" in formatted
        assert "Priority: HIGH" in formatted
        assert "Type: test" in formatted
        assert "sync_id" in formatted
        assert "AWS Contact Synchronization System" in formatted
    
    def test_format_sms_message(self):
        """Test SMS message formatting and truncation."""
        message = NotificationMessage(
            title="Very Long Test Notification Title That Exceeds SMS Limits",
            message="Very long test message body that definitely exceeds the 160 character limit for SMS messages and should be truncated appropriately",
            priority="high",
            notification_type="test",
            metadata={},
            timestamp=datetime.now(timezone.utc)
        )
        
        formatted = self.client._format_sms_message(message)
        
        assert len(formatted) <= 160
        assert formatted.endswith("...")
        assert "AWS Contact Sync:" in formatted


class TestMessageFormatter:
    """Test notification message formatting for different scenarios."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.contact_data = ContactInformation(
            address_line1="123 Test St",
            city="Test City",
            country_code="US",
            full_name="Test User",
            phone_number="+1-555-0123",
            postal_code="12345"
        )
        
        self.sync_operation = SyncOperation(
            sync_id="test-sync-123",
            timestamp=datetime.now(timezone.utc),
            initiating_user="arn:aws:iam::123456789012:user/test-user",
            contact_type="primary",
            source_account="123456789012",
            target_accounts=["234567890123", "345678901234"],
            status="completed",
            contact_data=self.contact_data,
            results={
                "234567890123": AccountSyncResult(
                    account_id="234567890123",
                    status="success",
                    timestamp=datetime.now(timezone.utc)
                ),
                "345678901234": AccountSyncResult(
                    account_id="345678901234",
                    status="failed",
                    timestamp=datetime.now(timezone.utc),
                    error_message="AccessDenied: Insufficient permissions"
                )
            }
        )
    
    def test_format_complete_failure(self):
        """Test formatting complete failure notification."""
        # Make all accounts fail
        for result in self.sync_operation.results.values():
            result.status = "failed"
            result.error_message = "ServiceUnavailable: API temporarily unavailable"
        
        message = NotificationMessageFormatter.format_complete_failure(self.sync_operation)
        
        assert message.title.startswith("AWS Contact Sync: Complete Failure")
        assert message.priority == "high"
        assert message.notification_type == "complete_failure"
        assert "ALL member accounts" in message.message
        assert "test-sync-123" in message.message
        assert message.metadata["sync_id"] == "test-sync-123"
        assert message.metadata["failed_count"] == 2
        assert message.metadata["failure_rate"] == 1.0
    
    def test_format_partial_failure(self):
        """Test formatting partial failure notification."""
        message = NotificationMessageFormatter.format_partial_failure(self.sync_operation)
        
        assert message.title.startswith("AWS Contact Sync: Partial Failure")
        assert message.priority == "medium"
        assert message.notification_type == "partial_failure"
        assert "partially failed" in message.message
        assert "Successful: 1" in message.message
        assert "Failed: 1" in message.message
        assert message.metadata["failed_count"] == 1
        assert message.metadata["successful_count"] == 1
        assert message.metadata["failure_rate"] == 0.5
    
    def test_format_success_completion(self):
        """Test formatting success completion notification."""
        # Make all accounts succeed
        for result in self.sync_operation.results.values():
            result.status = "success"
            result.error_message = None
        
        message = NotificationMessageFormatter.format_success_completion(self.sync_operation)
        
        assert message.title.startswith("AWS Contact Sync: Success")
        assert message.priority == "low"
        assert message.notification_type == "success_completion"
        assert "completed successfully" in message.message
        assert "Accounts Updated: 2" in message.message
        assert message.metadata["successful_count"] == 2
        assert message.metadata["failure_rate"] == 0.0
    
    def test_format_permission_errors(self):
        """Test formatting permission error notification."""
        permission_errors = [
            AccountSyncResult(
                account_id="345678901234",
                status="failed",
                timestamp=datetime.now(timezone.utc),
                error_message="AccessDenied: Insufficient permissions"
            )
        ]
        
        message = NotificationMessageFormatter.format_permission_errors(
            self.sync_operation, permission_errors
        )
        
        assert message.title.startswith("AWS Contact Sync: Permission Errors")
        assert message.priority == "high"
        assert message.notification_type == "permission_errors"
        assert "permission errors" in message.message
        assert "AccessDenied" in message.message
        assert message.metadata["permission_error_count"] == 1
    
    def test_format_system_errors(self):
        """Test formatting system error notification."""
        error_message = "Lambda function timeout after 15 minutes"
        
        message = NotificationMessageFormatter.format_system_errors(
            self.sync_operation, error_message
        )
        
        assert message.title.startswith("AWS Contact Sync: System Error")
        assert message.priority == "high"
        assert message.notification_type == "system_errors"
        assert "system error" in message.message
        assert error_message in message.message
        assert message.metadata["error_message"] == error_message
    
    def test_format_configuration_errors(self):
        """Test formatting configuration error notification."""
        error_message = "Invalid contact type specified"
        config_details = {
            "contact_types": ["invalid_type"],
            "excluded_accounts": ["123456789012"]
        }
        
        message = NotificationMessageFormatter.format_configuration_errors(
            error_message, config_details
        )
        
        assert message.title.startswith("AWS Contact Sync: Configuration Error")
        assert message.priority == "medium"
        assert message.notification_type == "configuration_errors"
        assert error_message in message.message
        assert "invalid_type" in message.message
        assert message.metadata["error_message"] == error_message
    
    def test_format_failed_accounts_truncation(self):
        """Test failed accounts formatting with truncation."""
        # Create more than 10 failed accounts
        failed_accounts = []
        for i in range(15):
            failed_accounts.append(
                AccountSyncResult(
                    account_id=f"{100000000000 + i:012d}",
                    status="failed",
                    timestamp=datetime.now(timezone.utc),
                    error_message=f"Error {i}"
                )
            )
        
        formatted = NotificationMessageFormatter._format_failed_accounts(failed_accounts)
        
        lines = formatted.split('\n')
        assert len(lines) == 11  # 10 accounts + "... and X more" line
        assert "and 5 more accounts" in lines[-1]


class TestNotificationHandler:
    """Test notification handler functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.mock_config_manager = Mock(spec=DynamoDBConfigManager)
        self.mock_config_manager.get_configuration.return_value = {
            "notification_settings": {
                "user_notifications_config": {
                    "notification_hub_region": "us-east-1",
                    "delivery_channels": ["EMAIL"],
                    "notification_rules": {
                        "high_priority": ["complete_failure", "permission_errors"],
                        "medium_priority": ["partial_failure"],
                        "low_priority": ["success_completion"]
                    }
                },
                "fallback_sns_topic": "arn:aws:sns:us-east-1:123456789012:fallback",
                "notify_on_failure": True,
                "notify_on_success": False,
                "notify_on_partial_failure": True,
                "failure_threshold": 1
            }
        }
        
        self.handler = NotificationHandler(self.mock_config_manager)
        
        self.sync_operation = SyncOperation(
            sync_id="test-sync-123",
            timestamp=datetime.now(timezone.utc),
            initiating_user="arn:aws:iam::123456789012:user/test-user",
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
            results={
                "234567890123": AccountSyncResult(
                    account_id="234567890123",
                    status="success",
                    timestamp=datetime.now(timezone.utc)
                )
            }
        )
    
    @patch('src.notifications.notification_handler.UserNotificationsClient')
    def test_handle_sync_completion_success(self, mock_client_class):
        """Test handling successful sync completion."""
        mock_client = Mock()
        mock_client.should_notify.return_value = True
        mock_client.send_notification.return_value = True
        mock_client_class.return_value = mock_client
        
        result = self.handler.handle_sync_completion(self.sync_operation)
        
        assert result is True
        mock_client.should_notify.assert_called_once_with("success_completion", 0, 1)
        mock_client.send_notification.assert_called_once()
    
    @patch('src.notifications.notification_handler.UserNotificationsClient')
    def test_handle_sync_completion_no_notification_needed(self, mock_client_class):
        """Test handling sync completion when no notification is needed."""
        mock_client = Mock()
        mock_client.should_notify.return_value = False
        mock_client_class.return_value = mock_client
        
        result = self.handler.handle_sync_completion(self.sync_operation)
        
        assert result is True
        mock_client.should_notify.assert_called_once()
        mock_client.send_notification.assert_not_called()
    
    def test_handle_sync_completion_no_client(self):
        """Test handling sync completion when no notification client is available."""
        # Return empty configuration
        self.mock_config_manager.get_configuration.return_value = {}
        
        result = self.handler.handle_sync_completion(self.sync_operation)
        
        assert result is False
    
    @patch('src.notifications.notification_handler.UserNotificationsClient')
    def test_send_with_retry_success_first_attempt(self, mock_client_class):
        """Test successful notification on first attempt."""
        mock_client = Mock()
        mock_client.send_notification.return_value = True
        
        result = self.handler._send_with_retry(mock_client, Mock())
        
        assert result is True
        mock_client.send_notification.assert_called_once()
    
    @patch('src.notifications.notification_handler.UserNotificationsClient')
    @patch('time.sleep')
    def test_send_with_retry_success_after_retry(self, mock_sleep, mock_client_class):
        """Test successful notification after retry."""
        mock_client = Mock()
        mock_client.send_notification.side_effect = [False, True]  # Fail then succeed
        
        result = self.handler._send_with_retry(mock_client, Mock(), max_retries=1)
        
        assert result is True
        assert mock_client.send_notification.call_count == 2
        mock_sleep.assert_called_once_with(1)  # 2^0 = 1 second delay
    
    @patch('src.notifications.notification_handler.UserNotificationsClient')
    @patch('time.sleep')
    def test_send_with_retry_all_attempts_fail(self, mock_sleep, mock_client_class):
        """Test notification failure after all retry attempts."""
        mock_client = Mock()
        mock_client.send_notification.return_value = False
        
        result = self.handler._send_with_retry(mock_client, Mock(), max_retries=2)
        
        assert result is False
        assert mock_client.send_notification.call_count == 3  # Initial + 2 retries
        assert mock_sleep.call_count == 2
    
    def test_is_permission_error_detection(self):
        """Test permission error detection."""
        permission_result = AccountSyncResult(
            account_id="123456789012",
            status="failed",
            timestamp=datetime.now(timezone.utc),
            error_message="AccessDenied: User does not have permission"
        )
        
        non_permission_result = AccountSyncResult(
            account_id="123456789012",
            status="failed",
            timestamp=datetime.now(timezone.utc),
            error_message="ServiceUnavailable: API temporarily unavailable"
        )
        
        assert self.handler._is_permission_error(permission_result) is True
        assert self.handler._is_permission_error(non_permission_result) is False
    
    def test_get_notification_status(self):
        """Test getting notification status."""
        status = self.handler.get_notification_status("test-sync-123")
        
        assert status["sync_id"] == "test-sync-123"
        assert "notification_enabled" in status
        assert "timestamp" in status
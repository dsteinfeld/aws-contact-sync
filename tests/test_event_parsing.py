"""Unit tests for CloudTrail event parsing and filtering logic.

Tests various CloudTrail event formats and validates critical filtering logic
for management account vs member account operations.

Requirements: 1.1, 1.2, 1.3
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock

from src.events.cloudtrail_parser import CloudTrailEventParser, ContactChangeEvent
from src.models.contact_models import ContactInformation, AlternateContact


class TestCloudTrailEventParsing:
    """Unit tests for CloudTrail event parsing functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.management_account_id = "123456789012"
        self.member_account_id = "234567890123"
        self.parser = CloudTrailEventParser(self.management_account_id)
    
    def test_parse_management_account_contact_information_event(self):
        """Test parsing PutContactInformation event from management account."""
        event = {
            "eventID": "test-event-123",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {
                "type": "IAMUser",
                "principalId": "AIDACKCEVSQ6C2EXAMPLE",
                "arn": "arn:aws:iam::123456789012:user/test-user",
                "accountId": self.management_account_id,
                "userName": "test-user"
            },
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101",
                    "stateOrRegion": "WA",
                    "companyName": "Example Corp"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_id == "test-event-123"
        assert result.event_name == "PutContactInformation"
        assert result.initiating_user == "arn:aws:iam::123456789012:user/test-user"
        assert result.source_account == self.management_account_id
        assert result.contact_type == "primary"
        assert result.is_management_account_change is True
        
        # Verify contact data
        assert isinstance(result.contact_data, ContactInformation)
        assert result.contact_data.full_name == "John Doe"
        assert result.contact_data.address_line1 == "123 Main Street"
        assert result.contact_data.city == "Seattle"
        assert result.contact_data.country_code == "US"
        assert result.contact_data.phone_number == "+1-206-555-0123"
        assert result.contact_data.postal_code == "98101"
        assert result.contact_data.state_or_region == "WA"
        assert result.contact_data.company_name == "Example Corp"
    
    def test_parse_management_account_alternate_contact_event(self):
        """Test parsing PutAlternateContact event from management account."""
        event = {
            "eventID": "test-event-456",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {
                "type": "AssumedRole",
                "principalId": "AROACKCEVSQ6C2EXAMPLE:session-name",
                "arn": "arn:aws:sts::123456789012:assumed-role/AdminRole/session-name",
                "accountId": self.management_account_id
            },
            "requestParameters": {
                "alternateContactType": "BILLING",
                "alternateContact": {
                    "emailAddress": "billing@example.com",
                    "name": "Jane Smith",
                    "phoneNumber": "+1-206-555-0456",
                    "title": "Billing Manager"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_id == "test-event-456"
        assert result.event_name == "PutAlternateContact"
        assert result.initiating_user == "arn:aws:sts::123456789012:assumed-role/AdminRole/session-name"
        assert result.source_account == self.management_account_id
        assert result.contact_type == "BILLING"
        assert result.is_management_account_change is True
        
        # Verify contact data
        assert isinstance(result.contact_data, AlternateContact)
        assert result.contact_data.contact_type == "BILLING"
        assert result.contact_data.email_address == "billing@example.com"
        assert result.contact_data.name == "Jane Smith"
        assert result.contact_data.phone_number == "+1-206-555-0456"
        assert result.contact_data.title == "Billing Manager"
    
    def test_filter_member_account_operations(self):
        """Test that events with accountId in requestParameters are filtered out (member account operations)."""
        event = {
            "eventID": "test-event-789",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Always management account
            "userIdentity": {
                "type": "IAMUser",
                "principalId": "AIDACKCEVSQ6C2EXAMPLE",
                "arn": "arn:aws:iam::123456789012:user/test-user",
                "accountId": self.management_account_id,
                "userName": "test-user"
            },
            "requestParameters": {
                # Critical: Presence of accountId indicates member account operation
                "accountId": self.member_account_id,
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        # Should be None because this is a member account operation
        assert result is None
    
    def test_filter_member_account_alternate_contact_operations(self):
        """Test that alternate contact events with accountId are filtered out."""
        event = {
            "eventID": "test-event-101112",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Always management account
            "userIdentity": {
                "type": "IAMUser",
                "principalId": "AIDACKCEVSQ6C2EXAMPLE",
                "arn": "arn:aws:iam::123456789012:user/test-user",
                "accountId": self.management_account_id,
                "userName": "test-user"
            },
            "requestParameters": {
                # Critical: Presence of accountId indicates member account operation
                "accountId": self.member_account_id,
                "alternateContactType": "OPERATIONS",
                "alternateContact": {
                    "emailAddress": "ops@example.com",
                    "name": "Operations Team",
                    "phoneNumber": "+1-206-555-0789",
                    "title": "Operations Manager"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        # Should be None because this is a member account operation
        assert result is None
    
    def test_recipient_account_id_always_management_account(self):
        """Test that recipientAccountId is always the management account ID in both scenarios."""
        # Management account operation
        mgmt_event = {
            "eventID": "test-event-mgmt",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        # Member account operation (should be filtered out)
        member_event = {
            "eventID": "test-event-member",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Still management account
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "accountId": self.member_account_id,  # Target is member account
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        mgmt_result = self.parser.parse_event(mgmt_event)
        member_result = self.parser.parse_event(member_event)
        
        # Management account operation should be processed
        assert mgmt_result is not None
        assert mgmt_result.source_account == self.management_account_id
        
        # Member account operation should be filtered out
        assert member_result is None
    
    def test_infinite_loop_prevention(self):
        """Test that the filtering logic prevents infinite loops."""
        # This test verifies that member account updates (which would be generated
        # by the sync service) are properly filtered out
        
        sync_generated_event = {
            "eventID": "test-event-sync",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {
                "type": "AssumedRole",
                "arn": "arn:aws:sts::123456789012:assumed-role/ContactSyncRole/lambda-session"
            },
            "requestParameters": {
                # This would be a member account update triggered by sync service
                "accountId": self.member_account_id,
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        result = self.parser.parse_event(sync_generated_event)
        
        # Should be filtered out to prevent infinite loops
        assert result is None
    
    def test_unsupported_event_names(self):
        """Test that unsupported event names are ignored."""
        unsupported_events = [
            "GetContactInformation",
            "DeleteAlternateContact",
            "ListAccounts",
            "CreateAccount"
        ]
        
        for event_name in unsupported_events:
            event = {
                "eventID": f"test-event-{event_name}",
                "eventName": event_name,
                "eventTime": "2024-01-09T10:00:00Z",
                "eventSource": "account.amazonaws.com",
                "recipientAccountId": self.management_account_id,
                "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
                "requestParameters": {}
            }
            
            result = self.parser.parse_event(event)
            assert result is None, f"Event {event_name} should be ignored"
    
    def test_invalid_event_structure(self):
        """Test handling of events with invalid structure."""
        invalid_events = [
            {},  # Empty event
            {"eventName": "PutContactInformation"},  # Missing required fields
            {
                "eventID": "test",
                "eventName": "PutContactInformation",
                "eventTime": "2024-01-09T10:00:00Z",
                # Missing userIdentity, recipientAccountId, requestParameters
            }
        ]
        
        for invalid_event in invalid_events:
            result = self.parser.parse_event(invalid_event)
            assert result is None
    
    def test_malformed_contact_data(self):
        """Test handling of events with malformed contact data."""
        event_with_missing_contact_fields = {
            "eventID": "test-event-malformed",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    # Missing required fields: city, countryCode, fullName, phoneNumber, postalCode
                }
            }
        }
        
        with pytest.raises(ValueError):
            self.parser.parse_event(event_with_missing_contact_fields)
    
    def test_invalid_alternate_contact_type(self):
        """Test handling of invalid alternate contact types."""
        event_with_invalid_contact_type = {
            "eventID": "test-event-invalid-type",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "alternateContactType": "INVALID_TYPE",
                "alternateContact": {
                    "emailAddress": "test@example.com",
                    "name": "Test Contact",
                    "phoneNumber": "+1-206-555-0123",
                    "title": "Test Title"
                }
            }
        }
        
        with pytest.raises(ValueError):
            self.parser.parse_event(event_with_invalid_contact_type)
    
    def test_parse_eventbridge_record(self):
        """Test parsing EventBridge record containing CloudTrail event."""
        cloudtrail_event = {
            "eventID": "test-event-eb",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        eventbridge_record = {
            "source": "aws.account",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": cloudtrail_event
        }
        
        result = self.parser.parse_eventbridge_record(eventbridge_record)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_name == "PutContactInformation"
        assert result.source_account == self.management_account_id
    
    def test_parse_lambda_event_multiple_records(self):
        """Test parsing Lambda event with multiple EventBridge records."""
        cloudtrail_event1 = {
            "eventID": "test-event-1",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Main Street",
                    "city": "Seattle",
                    "countryCode": "US",
                    "fullName": "John Doe",
                    "phoneNumber": "+1-206-555-0123",
                    "postalCode": "98101"
                }
            }
        }
        
        cloudtrail_event2 = {
            "eventID": "test-event-2",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "alternateContactType": "BILLING",
                "alternateContact": {
                    "emailAddress": "billing@example.com",
                    "name": "Billing Contact",
                    "phoneNumber": "+1-206-555-0456",
                    "title": "Billing Manager"
                }
            }
        }
        
        lambda_event = {
            "Records": [
                {"detail": cloudtrail_event1},
                {"detail": cloudtrail_event2},
                # Include a member account event that should be filtered out
                {"detail": {
                    "eventID": "test-event-3",
                    "eventName": "PutContactInformation",
                    "eventTime": "2024-01-09T10:00:00Z",
                    "eventSource": "account.amazonaws.com",
                    "recipientAccountId": self.management_account_id,
                    "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
                    "requestParameters": {
                        "accountId": self.member_account_id,  # Member account operation
                        "contactInformation": {
                            "addressLine1": "123 Main Street",
                            "city": "Seattle",
                            "countryCode": "US",
                            "fullName": "John Doe",
                            "phoneNumber": "+1-206-555-0123",
                            "postalCode": "98101"
                        }
                    }
                }}
            ]
        }
        
        results = self.parser.parse_lambda_event(lambda_event)
        
        # Should get 2 results (the member account event should be filtered out)
        assert len(results) == 2
        
        # Verify first event
        assert results[0].event_name == "PutContactInformation"
        assert results[0].contact_type == "primary"
        assert isinstance(results[0].contact_data, ContactInformation)
        
        # Verify second event
        assert results[1].event_name == "PutAlternateContact"
        assert results[1].contact_type == "BILLING"
        assert isinstance(results[1].contact_data, AlternateContact)
    
    def test_user_identity_extraction_variations(self):
        """Test extraction of user identity from different formats."""
        user_identity_variations = [
            {
                "type": "IAMUser",
                "arn": "arn:aws:iam::123456789012:user/test-user",
                "userName": "test-user"
            },
            {
                "type": "AssumedRole",
                "arn": "arn:aws:sts::123456789012:assumed-role/AdminRole/session"
            },
            {
                "type": "Root",
                "principalId": "123456789012"
            },
            {
                "type": "Unknown"
            }
        ]
        
        expected_users = [
            "arn:aws:iam::123456789012:user/test-user",
            "arn:aws:sts::123456789012:assumed-role/AdminRole/session",
            "Root:123456789012",
            "unknown"
        ]
        
        for i, user_identity in enumerate(user_identity_variations):
            event = {
                "eventID": f"test-event-user-{i}",
                "eventName": "PutContactInformation",
                "eventTime": "2024-01-09T10:00:00Z",
                "eventSource": "account.amazonaws.com",
                "recipientAccountId": self.management_account_id,
                "userIdentity": user_identity,
                "requestParameters": {
                    "contactInformation": {
                        "addressLine1": "123 Main Street",
                        "city": "Seattle",
                        "countryCode": "US",
                        "fullName": "John Doe",
                        "phoneNumber": "+1-206-555-0123",
                        "postalCode": "98101"
                    }
                }
            }
            
            result = self.parser.parse_event(event)
            assert result is not None
            assert result.initiating_user == expected_users[i]
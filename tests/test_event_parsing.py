"""Unit tests for CloudTrail event parsing and filtering logic.

Tests various CloudTrail event formats and validates infinite loop prevention.
Requirements: 1.1, 1.2, 1.3
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from src.events.cloudtrail_parser import CloudTrailEventParser, ContactChangeEvent
from src.events.eventbridge_config import ContactSyncEventBridgeConfig
from src.models.contact_models import ContactInformation, AlternateContact


class TestCloudTrailEventParsing:
    """Unit tests for CloudTrail event parsing functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.management_account_id = "123456789012"
        self.parser = CloudTrailEventParser(self.management_account_id)
        self.member_account_id = "234567890123"
    
    def test_parse_put_contact_information_management_account(self):
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
                "accountId": "123456789012",
                "userName": "test-user"
            },
            "requestParameters": {
                "contactInformation": {
                    "addressLine1": "123 Test Street",
                    "city": "Test City",
                    "countryCode": "US",
                    "fullName": "Test User",
                    "phoneNumber": "+1-555-123-4567",
                    "postalCode": "12345",
                    "companyName": "Test Company"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_name == "PutContactInformation"
        assert result.contact_type == "primary"
        assert result.source_account == self.management_account_id
        assert result.is_management_account_change is True
        assert isinstance(result.contact_data, ContactInformation)
        assert result.contact_data.full_name == "Test User"
        assert result.contact_data.company_name == "Test Company"
    
    def test_parse_put_alternate_contact_management_account(self):
        """Test parsing PutAlternateContact event from management account."""
        event = {
            "eventID": "test-event-456",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {
                "type": "AssumedRole",
                "principalId": "AROACKCEVSQ6C2EXAMPLE:test-session",
                "arn": "arn:aws:sts::123456789012:assumed-role/test-role/test-session"
            },
            "requestParameters": {
                "alternateContactType": "BILLING",
                "alternateContact": {
                    "emailAddress": "billing@example.com",
                    "name": "Billing Contact",
                    "phoneNumber": "+1-555-987-6543",
                    "title": "Billing Manager"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_name == "PutAlternateContact"
        assert result.contact_type == "BILLING"
        assert result.source_account == self.management_account_id
        assert result.is_management_account_change is True
        assert isinstance(result.contact_data, AlternateContact)
        assert result.contact_data.contact_type == "BILLING"
        assert result.contact_data.email_address == "billing@example.com"
    
    def test_ignore_member_account_operations(self):
        """Test that events with accountId in requestParameters are ignored (member account updates)."""
        event = {
            "eventID": "test-event-789",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Always management account
            "userIdentity": {
                "type": "IAMUser",
                "principalId": "AIDACKCEVSQ6C2EXAMPLE",
                "arn": "arn:aws:iam::123456789012:user/test-user"
            },
            "requestParameters": {
                "accountId": self.member_account_id,  # This indicates member account operation
                "contactInformation": {
                    "addressLine1": "123 Test Street",
                    "city": "Test City",
                    "countryCode": "US",
                    "fullName": "Test User",
                    "phoneNumber": "+1-555-123-4567",
                    "postalCode": "12345"
                }
            }
        }
        
        result = self.parser.parse_event(event)
        
        # Should be None because this is a member account operation
        assert result is None
    
    def test_ignore_member_account_alternate_contact_operations(self):
        """Test that alternate contact events with accountId are ignored."""
        event = {
            "eventID": "test-event-101112",
            "eventName": "PutAlternateContact",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Always management account
            "userIdentity": {
                "type": "IAMUser",
                "principalId": "AIDACKCEVSQ6C2EXAMPLE",
                "arn": "arn:aws:iam::123456789012:user/test-user"
            },
            "requestParameters": {
                "accountId": self.member_account_id,  # This indicates member account operation
                "alternateContactType": "OPERATIONS",
                "alternateContact": {
                    "emailAddress": "ops@example.com",
                    "name": "Operations Contact",
                    "phoneNumber": "+1-555-111-2222",
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
                    "addressLine1": "123 Test Street",
                    "city": "Test City",
                    "countryCode": "US",
                    "fullName": "Test User",
                    "phoneNumber": "+1-555-123-4567",
                    "postalCode": "12345"
                }
            }
        }
        
        # Member account operation (should be ignored)
        member_event = {
            "eventID": "test-event-member",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,  # Still management account
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "accountId": self.member_account_id,  # But targeting member account
                "contactInformation": {
                    "addressLine1": "123 Test Street",
                    "city": "Test City",
                    "countryCode": "US",
                    "fullName": "Test User",
                    "phoneNumber": "+1-555-123-4567",
                    "postalCode": "12345"
                }
            }
        }
        
        mgmt_result = self.parser.parse_event(mgmt_event)
        member_result = self.parser.parse_event(member_event)
        
        # Management account operation should be processed
        assert mgmt_result is not None
        assert mgmt_result.source_account == self.management_account_id
        
        # Member account operation should be ignored
        assert member_result is None
    
    def test_infinite_loop_prevention(self):
        """Test that the filtering logic prevents infinite loops."""
        # Simulate what happens when our system updates a member account
        # This should generate an event with accountId in requestParameters
        system_generated_event = {
            "eventID": "system-event-123",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {
                "type": "AssumedRole",
                "principalId": "AROACKCEVSQ6C2EXAMPLE:contact-sync-lambda",
                "arn": "arn:aws:sts::123456789012:assumed-role/ContactSyncRole/contact-sync-lambda"
            },
            "requestParameters": {
                "accountId": self.member_account_id,  # System updating member account
                "contactInformation": {
                    "addressLine1": "123 Test Street",
                    "city": "Test City",
                    "countryCode": "US",
                    "fullName": "Test User",
                    "phoneNumber": "+1-555-123-4567",
                    "postalCode": "12345"
                }
            }
        }
        
        result = self.parser.parse_event(system_generated_event)
        
        # This should be ignored to prevent infinite loops
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
                # Missing eventTime, userIdentity, etc.
            }
        ]
        
        for invalid_event in invalid_events:
            result = self.parser.parse_event(invalid_event)
            assert result is None
    
    def test_invalid_contact_data(self):
        """Test handling of events with invalid contact data."""
        event = {
            "eventID": "test-event-invalid",
            "eventName": "PutContactInformation",
            "eventTime": "2024-01-09T10:00:00Z",
            "eventSource": "account.amazonaws.com",
            "recipientAccountId": self.management_account_id,
            "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
            "requestParameters": {
                "contactInformation": {
                    # Missing required fields
                    "addressLine1": "123 Test Street"
                }
            }
        }
        
        with pytest.raises(ValueError, match="Failed to parse CloudTrail event"):
            self.parser.parse_event(event)
    
    def test_invalid_alternate_contact_type(self):
        """Test handling of invalid alternate contact types."""
        event = {
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
                    "phoneNumber": "+1-555-123-4567",
                    "title": "Test Title"
                }
            }
        }
        
        with pytest.raises(ValueError, match="Failed to parse CloudTrail event"):
            self.parser.parse_event(event)
    
    def test_parse_eventbridge_record(self):
        """Test parsing EventBridge record containing CloudTrail event."""
        eventbridge_record = {
            "eventSource": "aws:events",
            "eventName": "ContactChangeEvent",
            "detail": {
                "eventID": "test-event-eb",
                "eventName": "PutContactInformation",
                "eventTime": "2024-01-09T10:00:00Z",
                "eventSource": "account.amazonaws.com",
                "recipientAccountId": self.management_account_id,
                "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
                "requestParameters": {
                    "contactInformation": {
                        "addressLine1": "123 Test Street",
                        "city": "Test City",
                        "countryCode": "US",
                        "fullName": "Test User",
                        "phoneNumber": "+1-555-123-4567",
                        "postalCode": "12345"
                    }
                }
            }
        }
        
        result = self.parser.parse_eventbridge_record(eventbridge_record)
        
        assert result is not None
        assert isinstance(result, ContactChangeEvent)
        assert result.event_name == "PutContactInformation"
    
    def test_parse_lambda_event_multiple_records(self):
        """Test parsing Lambda event with multiple EventBridge records."""
        lambda_event = {
            "Records": [
                {
                    "eventSource": "aws:events",
                    "detail": {
                        "eventID": "test-event-1",
                        "eventName": "PutContactInformation",
                        "eventTime": "2024-01-09T10:00:00Z",
                        "eventSource": "account.amazonaws.com",
                        "recipientAccountId": self.management_account_id,
                        "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
                        "requestParameters": {
                            "contactInformation": {
                                "addressLine1": "123 Test Street",
                                "city": "Test City",
                                "countryCode": "US",
                                "fullName": "Test User",
                                "phoneNumber": "+1-555-123-4567",
                                "postalCode": "12345"
                            }
                        }
                    }
                },
                {
                    "eventSource": "aws:events",
                    "detail": {
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
                                "phoneNumber": "+1-555-987-6543",
                                "title": "Billing Manager"
                            }
                        }
                    }
                },
                {
                    # This record should be ignored (member account operation)
                    "eventSource": "aws:events",
                    "detail": {
                        "eventID": "test-event-3",
                        "eventName": "PutContactInformation",
                        "eventTime": "2024-01-09T10:00:00Z",
                        "eventSource": "account.amazonaws.com",
                        "recipientAccountId": self.management_account_id,
                        "userIdentity": {"type": "IAMUser", "arn": "arn:aws:iam::123456789012:user/test"},
                        "requestParameters": {
                            "accountId": self.member_account_id,  # Member account operation
                            "contactInformation": {
                                "addressLine1": "123 Test Street",
                                "city": "Test City",
                                "countryCode": "US",
                                "fullName": "Test User",
                                "phoneNumber": "+1-555-123-4567",
                                "postalCode": "12345"
                            }
                        }
                    }
                }
            ]
        }
        
        results = self.parser.parse_lambda_event(lambda_event)
        
        # Should get 2 results (first two records), third should be filtered out
        assert len(results) == 2
        assert results[0].event_name == "PutContactInformation"
        assert results[1].event_name == "PutAlternateContact"
        assert results[1].contact_type == "BILLING"


class TestEventBridgeConfiguration:
    """Unit tests for EventBridge rule configuration."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.management_account_id = "123456789012"
        self.lambda_arn = "arn:aws:lambda:us-east-1:123456789012:function:ContactSyncHandler"
        self.config = ContactSyncEventBridgeConfig(self.management_account_id, self.lambda_arn)
    
    def test_event_pattern_validation_management_account(self):
        """Test that management account events match the pattern."""
        cloudtrail_event = {
            "source": "aws.account",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventSource": "account.amazonaws.com",
                "eventName": "PutContactInformation",
                "recipientAccountId": self.management_account_id,
                "requestParameters": {
                    "contactInformation": {"fullName": "Test User"}
                }
            }
        }
        
        assert self.config.validate_event_pattern(cloudtrail_event) is True
    
    def test_event_pattern_validation_member_account_filtered(self):
        """Test that member account events are filtered out."""
        cloudtrail_event = {
            "source": "aws.account",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventSource": "account.amazonaws.com",
                "eventName": "PutContactInformation",
                "recipientAccountId": self.management_account_id,
                "requestParameters": {
                    "accountId": "234567890123",  # Member account operation
                    "contactInformation": {"fullName": "Test User"}
                }
            }
        }
        
        assert self.config.validate_event_pattern(cloudtrail_event) is False
    
    def test_event_pattern_validation_wrong_source(self):
        """Test that events from wrong sources are filtered out."""
        cloudtrail_event = {
            "source": "aws.ec2",  # Wrong source
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventSource": "account.amazonaws.com",
                "eventName": "PutContactInformation",
                "recipientAccountId": self.management_account_id,
                "requestParameters": {}
            }
        }
        
        assert self.config.validate_event_pattern(cloudtrail_event) is False
    
    def test_event_pattern_validation_unsupported_event(self):
        """Test that unsupported events are filtered out."""
        cloudtrail_event = {
            "source": "aws.account",
            "detail-type": "AWS API Call via CloudTrail",
            "detail": {
                "eventSource": "account.amazonaws.com",
                "eventName": "GetContactInformation",  # Unsupported event
                "recipientAccountId": self.management_account_id,
                "requestParameters": {}
            }
        }
        
        assert self.config.validate_event_pattern(cloudtrail_event) is False
    
    def test_sam_template_generation(self):
        """Test SAM template resource generation."""
        resources = self.config.get_sam_template_resources()
        
        assert "ContactSyncEventRule" in resources
        assert "ContactSyncLambdaPermission" in resources
        
        rule = resources["ContactSyncEventRule"]
        assert rule["Type"] == "AWS::Events::Rule"
        assert rule["Properties"]["Name"] == "ContactSyncRule"
        
        permission = resources["ContactSyncLambdaPermission"]
        assert permission["Type"] == "AWS::Lambda::Permission"
        assert permission["Properties"]["Action"] == "lambda:InvokeFunction"
    
    def test_cloudformation_template_generation(self):
        """Test complete CloudFormation template generation."""
        template = self.config.get_cloudformation_template()
        
        assert template["AWSTemplateFormatVersion"] == "2010-09-09"
        assert "Parameters" in template
        assert "Resources" in template
        assert "ManagementAccountId" in template["Parameters"]
        assert "LambdaFunctionArn" in template["Parameters"]
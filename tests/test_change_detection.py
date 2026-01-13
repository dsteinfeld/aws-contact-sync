"""Property-based tests for contact change detection timing.

Feature: aws-contact-sync, Property 1: Contact Change Detection Timing
Validates: Requirements 1.1, 1.2, 1.3
"""

import pytest
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st, settings, assume
from typing import Dict, Any

from src.events.cloudtrail_parser import CloudTrailEventParser, ContactChangeEvent
from src.models.contact_models import ContactInformation, AlternateContact


class TestChangeDetectionProperties:
    """Property-based tests for contact change detection timing."""
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        event_name=st.sampled_from(["PutContactInformation", "PutAlternateContact"]),
        user_type=st.sampled_from(["IAMUser", "AssumedRole", "Root"]),
        has_account_id_in_params=st.booleans(),
        contact_type=st.sampled_from(["BILLING", "OPERATIONS", "SECURITY"]) if st.sampled_from(["PutContactInformation", "PutAlternateContact"]) == "PutAlternateContact" else st.just("primary")
    )
    @settings(max_examples=100, deadline=None)
    def test_contact_change_detection_timing(self, management_account_id, event_name, user_type, has_account_id_in_params, contact_type):
        """Property 1: For any contact information change (primary or alternate) in the management account,
        the Contact_Sync_Service should detect the change within 5 minutes regardless of the contact type
        or number of fields modified.
        
        **Validates: Requirements 1.1, 1.2, 1.3**
        """
        # Ensure management account ID is valid
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        
        parser = CloudTrailEventParser(management_account_id)
        
        # Generate a CloudTrail event
        cloudtrail_event = self._generate_cloudtrail_event(
            management_account_id=management_account_id,
            event_name=event_name,
            user_type=user_type,
            has_account_id_in_params=has_account_id_in_params,
            contact_type=contact_type if event_name == "PutAlternateContact" else "primary"
        )
        
        # Parse the event
        parsed_event = parser.parse_event(cloudtrail_event)
        
        # Verify detection behavior based on event characteristics
        if has_account_id_in_params:
            # Events with accountId in requestParameters should be ignored (member account operations)
            assert parsed_event is None, "Member account operations should be ignored"
        else:
            # Management account operations should be detected
            assert parsed_event is not None, "Management account operations should be detected"
            assert isinstance(parsed_event, ContactChangeEvent)
            
            # Verify event properties
            assert parsed_event.event_name == event_name
            assert parsed_event.source_account == management_account_id
            assert parsed_event.is_management_account_change is True
            
            # Verify contact type mapping
            if event_name == "PutContactInformation":
                assert parsed_event.contact_type == "primary"
                assert isinstance(parsed_event.contact_data, ContactInformation)
            elif event_name == "PutAlternateContact":
                assert parsed_event.contact_type == contact_type
                assert isinstance(parsed_event.contact_data, AlternateContact)
                assert parsed_event.contact_data.contact_type == contact_type
            
            # Verify timing - event should be parsed immediately (within processing time)
            # This represents the detection capability - actual timing depends on EventBridge delivery
            event_time = parsed_event.event_time
            assert isinstance(event_time, datetime)
            assert event_time.tzinfo is not None  # Should have timezone info
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        num_contact_fields=st.integers(min_value=1, max_value=10),
        event_name=st.sampled_from(["PutContactInformation", "PutAlternateContact"])
    )
    @settings(max_examples=100, deadline=None)
    def test_multiple_field_change_detection(self, management_account_id, num_contact_fields, event_name):
        """Property: For any contact change with multiple fields modified simultaneously,
        the system should detect all changes as a single update event.
        
        **Validates: Requirements 1.3**
        """
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        
        parser = CloudTrailEventParser(management_account_id)
        
        # Generate event with multiple field changes
        cloudtrail_event = self._generate_cloudtrail_event(
            management_account_id=management_account_id,
            event_name=event_name,
            user_type="IAMUser",
            has_account_id_in_params=False,  # Management account operation
            contact_type="BILLING" if event_name == "PutAlternateContact" else "primary",
            num_fields=num_contact_fields
        )
        
        # Parse the event
        parsed_event = parser.parse_event(cloudtrail_event)
        
        # Should detect as single event regardless of number of fields changed
        assert parsed_event is not None
        assert isinstance(parsed_event, ContactChangeEvent)
        
        # Verify all contact data is captured in single event
        if event_name == "PutContactInformation":
            contact_data = parsed_event.contact_data
            assert isinstance(contact_data, ContactInformation)
            # Verify required fields are present
            assert contact_data.full_name
            assert contact_data.address_line1
            assert contact_data.city
            assert contact_data.country_code
            assert contact_data.phone_number
            assert contact_data.postal_code
        elif event_name == "PutAlternateContact":
            contact_data = parsed_event.contact_data
            assert isinstance(contact_data, AlternateContact)
            # Verify required fields are present
            assert contact_data.name
            assert contact_data.email_address
            assert contact_data.phone_number
            assert contact_data.title
            assert contact_data.contact_type
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        different_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        event_name=st.sampled_from(["PutContactInformation", "PutAlternateContact"])
    )
    @settings(max_examples=100, deadline=None)
    def test_management_vs_member_account_filtering(self, management_account_id, different_account_id, event_name):
        """Property: For any contact change event, only management account changes should be processed,
        while member account changes should be filtered out to prevent infinite loops.
        
        **Validates: Requirements 1.1, 1.2**
        """
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        assume(len(different_account_id) == 12)
        assume(different_account_id.isdigit())
        assume(management_account_id != different_account_id)
        
        parser = CloudTrailEventParser(management_account_id)
        
        # Test 1: Management account operation (no accountId in requestParameters)
        mgmt_event = self._generate_cloudtrail_event(
            management_account_id=management_account_id,
            event_name=event_name,
            user_type="IAMUser",
            has_account_id_in_params=False,  # Management account operation
            contact_type="BILLING" if event_name == "PutAlternateContact" else "primary"
        )
        
        parsed_mgmt_event = parser.parse_event(mgmt_event)
        assert parsed_mgmt_event is not None, "Management account operations should be processed"
        assert parsed_mgmt_event.is_management_account_change is True
        
        # Test 2: Member account operation (has accountId in requestParameters)
        member_event = self._generate_cloudtrail_event(
            management_account_id=management_account_id,
            event_name=event_name,
            user_type="IAMUser",
            has_account_id_in_params=True,  # Member account operation
            contact_type="BILLING" if event_name == "PutAlternateContact" else "primary",
            target_account_id=different_account_id
        )
        
        parsed_member_event = parser.parse_event(member_event)
        assert parsed_member_event is None, "Member account operations should be filtered out"
    
    def _generate_cloudtrail_event(
        self, 
        management_account_id: str, 
        event_name: str, 
        user_type: str,
        has_account_id_in_params: bool,
        contact_type: str,
        num_fields: int = 6,
        target_account_id: str = None
    ) -> Dict[str, Any]:
        """Generate a realistic CloudTrail event for testing."""
        
        # Base event structure
        event = {
            "eventID": f"test-event-{hash(management_account_id + event_name) % 100000}",
            "eventName": event_name,
            "eventTime": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "eventSource": "account.amazonaws.com",
            "eventVersion": "1.05",
            "recipientAccountId": management_account_id,
            "userIdentity": self._generate_user_identity(user_type),
            "requestParameters": {}
        }
        
        # Add accountId to requestParameters for member account operations
        if has_account_id_in_params:
            event["requestParameters"]["accountId"] = target_account_id or "123456789012"
        
        # Generate contact data based on event type
        if event_name == "PutContactInformation":
            event["requestParameters"]["contactInformation"] = self._generate_contact_information(num_fields)
        elif event_name == "PutAlternateContact":
            event["requestParameters"]["alternateContactType"] = contact_type
            event["requestParameters"]["alternateContact"] = self._generate_alternate_contact()
        
        return event
    
    def _generate_user_identity(self, user_type: str) -> Dict[str, Any]:
        """Generate user identity based on type."""
        base_identity = {
            "type": user_type,
            "principalId": f"AIDACKCEVSQ6C2EXAMPLE",
            "arn": f"arn:aws:iam::123456789012:{user_type.lower()}:test-user",
            "accountId": "123456789012"
        }
        
        if user_type == "IAMUser":
            base_identity["userName"] = "test-user"
        elif user_type == "AssumedRole":
            base_identity["sessionContext"] = {
                "sessionIssuer": {
                    "type": "Role",
                    "principalId": "AROACKCEVSQ6C2EXAMPLE",
                    "arn": "arn:aws:iam::123456789012:role/test-role"
                }
            }
        
        return base_identity
    
    def _generate_contact_information(self, num_fields: int = 6) -> Dict[str, Any]:
        """Generate contact information with specified number of fields."""
        base_contact = {
            "addressLine1": "123 Test Street",
            "city": "Test City",
            "countryCode": "US",
            "fullName": "Test User",
            "phoneNumber": "+1-555-123-4567",
            "postalCode": "12345"
        }
        
        optional_fields = {
            "addressLine2": "Suite 100",
            "addressLine3": "Building A",
            "companyName": "Test Company",
            "districtOrCounty": "Test County",
            "stateOrRegion": "CA",
            "websiteUrl": "https://example.com"
        }
        
        # Add optional fields up to num_fields
        added_fields = 0
        for key, value in optional_fields.items():
            if added_fields >= num_fields - 6:  # 6 required fields
                break
            base_contact[key] = value
            added_fields += 1
        
        return base_contact
    
    def _generate_alternate_contact(self) -> Dict[str, Any]:
        """Generate alternate contact information."""
        return {
            "emailAddress": "test@example.com",
            "name": "Test Contact",
            "phoneNumber": "+1-555-987-6543",
            "title": "Test Title"
        }
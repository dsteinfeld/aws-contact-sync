"""CloudTrail event parser for Account Management API events."""

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Any, Optional, List, Union, Literal
from ..models.contact_models import ContactInformation, AlternateContact

logger = logging.getLogger(__name__)


@dataclass
class ContactChangeEvent:
    """Parsed contact change event from CloudTrail."""
    event_id: str
    event_time: datetime
    event_name: str  # PutContactInformation or PutAlternateContact
    initiating_user: str
    source_account: str
    contact_type: str  # "primary", "BILLING", "OPERATIONS", "SECURITY"
    contact_data: Union[ContactInformation, AlternateContact]
    is_management_account_change: bool

    def __post_init__(self):
        """Validate fields."""
        if not self.event_id.strip():
            raise ValueError("event_id cannot be empty")
        if not self.event_name.strip():
            raise ValueError("event_name cannot be empty")
        if self.event_name not in ["PutContactInformation", "PutAlternateContact"]:
            raise ValueError(f"Invalid event_name: {self.event_name}")
        if not self.initiating_user.strip():
            raise ValueError("initiating_user cannot be empty")
        if not self.source_account.strip():
            raise ValueError("source_account cannot be empty")
        if not self.contact_type.strip():
            raise ValueError("contact_type cannot be empty")


class CloudTrailEventParser:
    """Parser for CloudTrail events containing Account Management API calls."""

    # Account Management API events we care about
    SUPPORTED_EVENTS = {
        "PutContactInformation",
        "PutAlternateContact"
    }

    def __init__(self, management_account_id: str):
        """Initialize parser with management account ID for filtering."""
        self.management_account_id = management_account_id

    def parse_event(self, cloudtrail_event: Dict[str, Any]) -> Optional[ContactChangeEvent]:
        """
        Parse a CloudTrail event and extract contact change information.
        
        Args:
            cloudtrail_event: Raw CloudTrail event from EventBridge
            
        Returns:
            ContactChangeEvent if this is a relevant management account contact change,
            None if the event should be ignored
            
        Raises:
            ValueError: If event structure is invalid
        """
        try:
            # Validate basic event structure
            if not self._is_valid_event_structure(cloudtrail_event):
                logger.debug(f"Invalid event structure: {cloudtrail_event.get('eventName', 'unknown')}")
                return None

            event_name = cloudtrail_event["eventName"]
            
            # Only process supported Account Management events
            if event_name not in self.SUPPORTED_EVENTS:
                logger.debug(f"Unsupported event: {event_name}")
                return None

            # Critical filtering: Only process management account changes
            # Management account operations have NO accountId in requestParameters
            request_params = cloudtrail_event.get("requestParameters", {})
            if "accountId" in request_params:
                logger.debug(f"Ignoring member account operation: {event_name}")
                return None

            # Verify this is from the management account
            recipient_account_id = cloudtrail_event.get("recipientAccountId")
            if recipient_account_id != self.management_account_id:
                logger.debug(f"Event not from management account: {recipient_account_id}")
                return None

            # Extract event metadata
            event_id = cloudtrail_event["eventID"]
            event_time = datetime.fromisoformat(
                cloudtrail_event["eventTime"].replace("Z", "+00:00")
            )
            initiating_user = self._extract_user_identity(cloudtrail_event)
            source_account = recipient_account_id

            # Parse contact data based on event type
            if event_name == "PutContactInformation":
                contact_data, contact_type = self._parse_contact_information(request_params)
            elif event_name == "PutAlternateContact":
                contact_data, contact_type = self._parse_alternate_contact(request_params)
            else:
                logger.error(f"Unexpected event name: {event_name}")
                return None

            return ContactChangeEvent(
                event_id=event_id,
                event_time=event_time,
                event_name=event_name,
                initiating_user=initiating_user,
                source_account=source_account,
                contact_type=contact_type,
                contact_data=contact_data,
                is_management_account_change=True
            )

        except Exception as e:
            logger.error(f"Error parsing CloudTrail event: {e}")
            logger.debug(f"Event data: {json.dumps(cloudtrail_event, default=str)}")
            raise ValueError(f"Failed to parse CloudTrail event: {e}")

    def _is_valid_event_structure(self, event: Dict[str, Any]) -> bool:
        """Validate that the event has required CloudTrail fields."""
        required_fields = [
            "eventID", "eventName", "eventTime", "userIdentity", 
            "recipientAccountId", "requestParameters"
        ]
        
        for field in required_fields:
            if field not in event:
                logger.debug(f"Missing required field: {field}")
                return False
                
        return True

    def _extract_user_identity(self, event: Dict[str, Any]) -> str:
        """Extract the user identity who initiated the change."""
        user_identity = event.get("userIdentity", {})
        
        # Try different user identity formats
        if "arn" in user_identity:
            return user_identity["arn"]
        elif "userName" in user_identity:
            return user_identity["userName"]
        elif "type" in user_identity and "principalId" in user_identity:
            return f"{user_identity['type']}:{user_identity['principalId']}"
        else:
            return "unknown"

    def _parse_contact_information(self, request_params: Dict[str, Any]) -> tuple[ContactInformation, str]:
        """Parse PutContactInformation request parameters."""
        contact_info = request_params.get("contactInformation", {})
        
        if not contact_info:
            raise ValueError("Missing contactInformation in request parameters")

        # Extract required fields
        try:
            contact_data = ContactInformation(
                address_line1=contact_info["addressLine1"],
                city=contact_info["city"],
                country_code=contact_info["countryCode"],
                full_name=contact_info["fullName"],
                phone_number=contact_info["phoneNumber"],
                postal_code=contact_info["postalCode"],
                address_line2=contact_info.get("addressLine2"),
                address_line3=contact_info.get("addressLine3"),
                company_name=contact_info.get("companyName"),
                district_or_county=contact_info.get("districtOrCounty"),
                state_or_region=contact_info.get("stateOrRegion"),
                website_url=contact_info.get("websiteUrl")
            )
            return contact_data, "primary"
        except KeyError as e:
            raise ValueError(f"Missing required contact information field: {e}")

    def _parse_alternate_contact(self, request_params: Dict[str, Any]) -> tuple[AlternateContact, str]:
        """Parse PutAlternateContact request parameters."""
        alternate_contact = request_params.get("alternateContact", {})
        contact_type = request_params.get("alternateContactType")
        
        if not alternate_contact:
            raise ValueError("Missing alternateContact in request parameters")
        if not contact_type:
            raise ValueError("Missing alternateContactType in request parameters")

        # Validate contact type
        if contact_type not in ["BILLING", "OPERATIONS", "SECURITY"]:
            raise ValueError(f"Invalid alternate contact type: {contact_type}")

        try:
            contact_data = AlternateContact(
                contact_type=contact_type,
                email_address=alternate_contact["emailAddress"],
                name=alternate_contact["name"],
                phone_number=alternate_contact["phoneNumber"],
                title=alternate_contact["title"]
            )
            return contact_data, contact_type
        except KeyError as e:
            raise ValueError(f"Missing required alternate contact field: {e}")

    def parse_eventbridge_record(self, eventbridge_record: Dict[str, Any]) -> Optional[ContactChangeEvent]:
        """
        Parse an EventBridge record containing a CloudTrail event.
        
        Args:
            eventbridge_record: EventBridge record from Lambda event
            
        Returns:
            ContactChangeEvent if valid, None if should be ignored
        """
        try:
            # EventBridge wraps the CloudTrail event in a detail field
            detail = eventbridge_record.get("detail")
            if not detail:
                logger.debug("No detail field in EventBridge record")
                return None
                
            return self.parse_event(detail)
            
        except Exception as e:
            logger.error(f"Error parsing EventBridge record: {e}")
            raise

    def parse_lambda_event(self, lambda_event: Dict[str, Any]) -> List[ContactChangeEvent]:
        """
        Parse a Lambda event containing one or more EventBridge records.
        
        Args:
            lambda_event: Complete Lambda event payload
            
        Returns:
            List of ContactChangeEvent objects (may be empty)
        """
        events = []
        records = lambda_event.get("Records", [])
        
        for record in records:
            try:
                parsed_event = self.parse_eventbridge_record(record)
                if parsed_event:
                    events.append(parsed_event)
            except Exception as e:
                logger.error(f"Error processing record: {e}")
                # Continue processing other records
                continue
                
        return events
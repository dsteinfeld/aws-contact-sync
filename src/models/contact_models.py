"""Contact information data models."""

from dataclasses import dataclass
from typing import Optional, Literal


@dataclass
class ContactInformation:
    """Primary contact information model based on AWS Account Management API."""
    address_line1: str
    city: str
    country_code: str
    full_name: str
    phone_number: str
    postal_code: str
    address_line2: Optional[str] = None
    address_line3: Optional[str] = None
    company_name: Optional[str] = None
    district_or_county: Optional[str] = None
    state_or_region: Optional[str] = None
    website_url: Optional[str] = None

    def __post_init__(self):
        """Validate required fields."""
        if not self.address_line1.strip():
            raise ValueError("address_line1 cannot be empty")
        if not self.city.strip():
            raise ValueError("city cannot be empty")
        if not self.country_code.strip():
            raise ValueError("country_code cannot be empty")
        if not self.full_name.strip():
            raise ValueError("full_name cannot be empty")
        if not self.phone_number.strip():
            raise ValueError("phone_number cannot be empty")
        if not self.postal_code.strip():
            raise ValueError("postal_code cannot be empty")


@dataclass
class AlternateContact:
    """Alternate contact information model."""
    contact_type: Literal["BILLING", "OPERATIONS", "SECURITY"]
    email_address: str
    name: str
    phone_number: str
    title: str

    def __post_init__(self):
        """Validate required fields."""
        if self.contact_type not in ["BILLING", "OPERATIONS", "SECURITY"]:
            raise ValueError(f"Invalid contact_type: {self.contact_type}")
        if not self.email_address.strip():
            raise ValueError("email_address cannot be empty")
        if not self.name.strip():
            raise ValueError("name cannot be empty")
        if not self.phone_number.strip():
            raise ValueError("phone_number cannot be empty")
        if not self.title.strip():
            raise ValueError("title cannot be empty")
        # Basic email validation
        if "@" not in self.email_address:
            raise ValueError("email_address must contain @")
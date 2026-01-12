"""Synchronization operation data models."""

from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Union, Literal
from .contact_models import ContactInformation, AlternateContact


@dataclass
class AccountSyncResult:
    """Result of synchronizing contact information to a single account."""
    account_id: str
    status: Literal["success", "failed", "skipped"]
    timestamp: datetime
    error_message: Optional[str] = None
    retry_count: int = 0

    def __post_init__(self):
        """Validate fields."""
        if not self.account_id.strip():
            raise ValueError("account_id cannot be empty")
        if self.status not in ["success", "failed", "skipped"]:
            raise ValueError(f"Invalid status: {self.status}")
        if self.retry_count < 0:
            raise ValueError("retry_count cannot be negative")


@dataclass
class SyncOperation:
    """Complete synchronization operation tracking."""
    sync_id: str
    timestamp: datetime
    initiating_user: str
    contact_type: str
    source_account: str
    target_accounts: List[str]
    status: Literal["pending", "in_progress", "completed", "failed"]
    contact_data: Union[ContactInformation, AlternateContact]
    results: Dict[str, AccountSyncResult]

    def __post_init__(self):
        """Validate fields."""
        if not self.sync_id.strip():
            raise ValueError("sync_id cannot be empty")
        if not self.initiating_user.strip():
            raise ValueError("initiating_user cannot be empty")
        if not self.contact_type.strip():
            raise ValueError("contact_type cannot be empty")
        if not self.source_account.strip():
            raise ValueError("source_account cannot be empty")
        if self.status not in ["pending", "in_progress", "completed", "failed"]:
            raise ValueError(f"Invalid status: {self.status}")
        if not self.target_accounts:
            raise ValueError("target_accounts cannot be empty")
        if not isinstance(self.contact_data, (ContactInformation, AlternateContact)):
            raise ValueError("contact_data must be ContactInformation or AlternateContact")
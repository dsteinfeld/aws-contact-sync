"""Configuration management utilities."""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import json


@dataclass
class RetryConfig:
    """Retry configuration settings."""
    max_attempts: int = 3
    base_delay: int = 2
    max_delay: int = 60

    def __post_init__(self):
        """Validate retry configuration."""
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 1:
            raise ValueError("base_delay must be at least 1")
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be >= base_delay")


@dataclass
class NotificationSettings:
    """Notification configuration settings."""
    user_notifications_config: Dict[str, Any] = field(default_factory=dict)
    fallback_sns_topic: Optional[str] = None
    notify_on_failure: bool = True
    notify_on_success: bool = False
    notify_on_partial_failure: bool = True
    failure_threshold: int = 1

    def __post_init__(self):
        """Validate notification settings."""
        if self.failure_threshold < 0:
            raise ValueError("failure_threshold cannot be negative")


@dataclass
class SyncConfig:
    """Complete synchronization configuration."""
    contact_types: List[str] = field(default_factory=lambda: ["primary", "billing", "operations", "security"])
    excluded_accounts: List[str] = field(default_factory=list)
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    notification_settings: NotificationSettings = field(default_factory=NotificationSettings)

    def __post_init__(self):
        """Validate configuration."""
        valid_contact_types = ["primary", "billing", "operations", "security"]
        for contact_type in self.contact_types:
            # Case-insensitive comparison
            if contact_type.lower() not in valid_contact_types:
                raise ValueError(f"Invalid contact_type: {contact_type}")
        
        # Validate account IDs format (basic validation)
        for account_id in self.excluded_accounts:
            if not account_id.isdigit() or len(account_id) != 12:
                raise ValueError(f"Invalid AWS account ID format: {account_id}")

    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary."""
        return {
            "contact_types": self.contact_types,
            "excluded_accounts": self.excluded_accounts,
            "retry_config": {
                "max_attempts": self.retry_config.max_attempts,
                "base_delay": self.retry_config.base_delay,
                "max_delay": self.retry_config.max_delay
            },
            "notification_settings": {
                "user_notifications_config": self.notification_settings.user_notifications_config,
                "fallback_sns_topic": self.notification_settings.fallback_sns_topic,
                "notify_on_failure": self.notification_settings.notify_on_failure,
                "notify_on_success": self.notification_settings.notify_on_success,
                "notify_on_partial_failure": self.notification_settings.notify_on_partial_failure,
                "failure_threshold": self.notification_settings.failure_threshold
            }
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SyncConfig":
        """Create configuration from dictionary."""
        retry_data = data.get("retry_config", {})
        retry_config = RetryConfig(
            max_attempts=retry_data.get("max_attempts", 3),
            base_delay=retry_data.get("base_delay", 2),
            max_delay=retry_data.get("max_delay", 60)
        )
        
        notification_data = data.get("notification_settings", {})
        notification_settings = NotificationSettings(
            user_notifications_config=notification_data.get("user_notifications_config", {}),
            fallback_sns_topic=notification_data.get("fallback_sns_topic"),
            notify_on_failure=notification_data.get("notify_on_failure", True),
            notify_on_success=notification_data.get("notify_on_success", False),
            notify_on_partial_failure=notification_data.get("notify_on_partial_failure", True),
            failure_threshold=notification_data.get("failure_threshold", 1)
        )
        
        return cls(
            contact_types=data.get("contact_types", ["primary", "billing", "operations", "security"]),
            excluded_accounts=data.get("excluded_accounts", []),
            retry_config=retry_config,
            notification_settings=notification_settings
        )


class ConfigManager:
    """Manages synchronization configuration with validation."""
    
    def __init__(self):
        """Initialize configuration manager."""
        self._config: Optional[SyncConfig] = None
    
    def load_config(self, config_data: Dict[str, Any]) -> SyncConfig:
        """Load and validate configuration from dictionary."""
        try:
            self._config = SyncConfig.from_dict(config_data)
            return self._config
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid configuration: {e}")
    
    def get_config(self) -> Optional[SyncConfig]:
        """Get current configuration."""
        return self._config
    
    def validate_config(self, config_data: Dict[str, Any]) -> bool:
        """Validate configuration without loading it."""
        try:
            SyncConfig.from_dict(config_data)
            return True
        except (ValueError, TypeError):
            return False
    
    def update_config(self, updates: Dict[str, Any]) -> SyncConfig:
        """Update existing configuration with new values."""
        if self._config is None:
            raise ValueError("No configuration loaded")
        
        current_dict = self._config.to_dict()
        current_dict.update(updates)
        
        return self.load_config(current_dict)
"""Property-based tests for configuration validation and isolation.

Feature: aws-contact-sync, Property 7: Configuration Validation and Isolation
Validates: Requirements 5.3, 5.4
"""

import pytest
from hypothesis import given, strategies as st, assume
from src.config import ConfigManager, SyncConfig


# Hypothesis strategies for generating test data
valid_contact_types = st.lists(
    st.sampled_from(["primary", "billing", "operations", "security"]),
    min_size=1,
    max_size=4,
    unique=True
)

valid_account_ids = st.lists(
    st.text(alphabet="0123456789", min_size=12, max_size=12),
    max_size=10
)

invalid_account_ids = st.one_of(
    st.text(alphabet="0123456789", min_size=1, max_size=11),  # Too short
    st.text(alphabet="0123456789", min_size=13, max_size=20),  # Too long
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=12, max_size=12),  # Non-numeric
    st.text(min_size=0, max_size=0)  # Empty
)

valid_retry_config = st.fixed_dictionaries({
    "max_attempts": st.integers(min_value=1, max_value=10),
    "base_delay": st.integers(min_value=1, max_value=30),
    "max_delay": st.integers(min_value=30, max_value=300)
})

invalid_retry_config = st.one_of(
    st.fixed_dictionaries({
        "max_attempts": st.integers(max_value=0),  # Invalid: <= 0
        "base_delay": st.integers(min_value=1, max_value=30),
        "max_delay": st.integers(min_value=30, max_value=300)
    }),
    st.fixed_dictionaries({
        "max_attempts": st.integers(min_value=1, max_value=10),
        "base_delay": st.integers(max_value=0),  # Invalid: <= 0
        "max_delay": st.integers(min_value=30, max_value=300)
    }),
    st.fixed_dictionaries({
        "max_attempts": st.integers(min_value=1, max_value=10),
        "base_delay": st.integers(min_value=30, max_value=60),
        "max_delay": st.integers(min_value=1, max_value=29)  # Invalid: < base_delay
    })
)


@pytest.mark.property
class TestConfigurationValidationAndIsolation:
    """Property 7: Configuration Validation and Isolation
    
    For any configuration change, invalid configurations should be rejected 
    before application, and changes should only affect future operations 
    without disrupting in-progress synchronizations.
    """

    @given(
        contact_types=valid_contact_types,
        excluded_accounts=valid_account_ids,
        retry_config=valid_retry_config
    )
    def test_valid_configurations_are_accepted(self, contact_types, excluded_accounts, retry_config):
        """Valid configurations should be accepted and loaded successfully."""
        # Ensure max_delay >= base_delay for valid retry config
        if retry_config["max_delay"] < retry_config["base_delay"]:
            retry_config["max_delay"] = retry_config["base_delay"]
        
        config_data = {
            "contact_types": contact_types,
            "excluded_accounts": excluded_accounts,
            "retry_config": retry_config,
            "notification_settings": {
                "notify_on_failure": True,
                "failure_threshold": 1
            }
        }
        
        manager = ConfigManager()
        
        # Configuration should be validated and accepted
        assert manager.validate_config(config_data) is True
        
        # Configuration should load without errors
        loaded_config = manager.load_config(config_data)
        assert loaded_config is not None
        assert loaded_config.contact_types == contact_types
        assert loaded_config.excluded_accounts == excluded_accounts

    @given(invalid_account_ids=st.lists(invalid_account_ids, min_size=1, max_size=5))
    def test_invalid_account_ids_are_rejected(self, invalid_account_ids):
        """Invalid account IDs should be rejected during validation."""
        config_data = {
            "contact_types": ["primary"],
            "excluded_accounts": invalid_account_ids,
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60}
        }
        
        manager = ConfigManager()
        
        # Invalid configuration should be rejected
        assert manager.validate_config(config_data) is False
        
        # Loading should raise ValueError
        with pytest.raises(ValueError, match="Invalid configuration"):
            manager.load_config(config_data)

    @given(invalid_contact_types=st.lists(
        st.text().filter(lambda x: x not in ["primary", "billing", "operations", "security"]),
        min_size=1,
        max_size=3
    ))
    def test_invalid_contact_types_are_rejected(self, invalid_contact_types):
        """Invalid contact types should be rejected during validation."""
        assume(all(ct.strip() for ct in invalid_contact_types))  # Avoid empty strings
        
        config_data = {
            "contact_types": invalid_contact_types,
            "excluded_accounts": [],
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60}
        }
        
        manager = ConfigManager()
        
        # Invalid configuration should be rejected
        assert manager.validate_config(config_data) is False
        
        # Loading should raise ValueError
        with pytest.raises(ValueError, match="Invalid configuration"):
            manager.load_config(config_data)

    @given(retry_config=invalid_retry_config)
    def test_invalid_retry_config_is_rejected(self, retry_config):
        """Invalid retry configurations should be rejected during validation."""
        config_data = {
            "contact_types": ["primary"],
            "excluded_accounts": [],
            "retry_config": retry_config
        }
        
        manager = ConfigManager()
        
        # Invalid configuration should be rejected
        assert manager.validate_config(config_data) is False
        
        # Loading should raise ValueError
        with pytest.raises(ValueError, match="Invalid configuration"):
            manager.load_config(config_data)

    @given(
        initial_config=st.fixed_dictionaries({
            "contact_types": valid_contact_types,
            "excluded_accounts": valid_account_ids,
            "retry_config": valid_retry_config
        }),
        update_data=st.fixed_dictionaries({
            "contact_types": valid_contact_types,
            "excluded_accounts": valid_account_ids
        })
    )
    def test_configuration_updates_preserve_isolation(self, initial_config, update_data):
        """Configuration updates should not affect the original configuration object."""
        # Ensure valid retry config
        if initial_config["retry_config"]["max_delay"] < initial_config["retry_config"]["base_delay"]:
            initial_config["retry_config"]["max_delay"] = initial_config["retry_config"]["base_delay"]
        
        manager = ConfigManager()
        
        # Load initial configuration
        original_config = manager.load_config(initial_config)
        original_contact_types = original_config.contact_types.copy()
        original_excluded_accounts = original_config.excluded_accounts.copy()
        
        # Update configuration
        updated_config = manager.update_config(update_data)
        
        # Original configuration should remain unchanged (isolation)
        assert original_config.contact_types == original_contact_types
        assert original_config.excluded_accounts == original_excluded_accounts
        
        # Updated configuration should reflect changes
        assert updated_config.contact_types == update_data["contact_types"]
        assert updated_config.excluded_accounts == update_data["excluded_accounts"]
        
        # Configurations should be different objects
        assert original_config is not updated_config

    def test_configuration_validation_without_loading(self):
        """Configuration validation should work without loading the configuration."""
        manager = ConfigManager()
        
        # Should be able to validate without loading
        assert manager.get_config() is None
        
        valid_config = {
            "contact_types": ["primary", "billing"],
            "excluded_accounts": ["123456789012"],
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60}
        }
        
        # Validation should work without affecting internal state
        assert manager.validate_config(valid_config) is True
        assert manager.get_config() is None  # Still no config loaded
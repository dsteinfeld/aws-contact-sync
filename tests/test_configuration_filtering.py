"""Property-based tests for configuration-based filtering.

Feature: aws-contact-sync, Property 6: Configuration-Based Filtering
Validates: Requirements 5.1, 5.2
"""

import pytest
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st, assume
from src.config.dynamodb_config_manager import DynamoDBConfigManager
from src.config.config_manager import SyncConfig


# Hypothesis strategies for generating test data
all_contact_types = ["primary", "billing", "operations", "security"]
contact_type_subsets = st.lists(
    st.sampled_from(all_contact_types),
    min_size=1,
    max_size=4,
    unique=True
)

account_ids = st.lists(
    st.text(alphabet="0123456789", min_size=12, max_size=12),
    min_size=0,
    max_size=20,
    unique=True
)

test_contact_types = st.sampled_from(all_contact_types)


@pytest.mark.property
class TestConfigurationBasedFiltering:
    """Property 6: Configuration-Based Filtering
    
    For any synchronization operation, only contact types specified in the 
    configuration should be synchronized, and accounts in the exclusion list 
    should be skipped entirely.
    """

    @given(
        configured_contact_types=contact_type_subsets,
        test_contact_type=test_contact_types
    )
    def test_contact_type_filtering_respects_configuration(self, configured_contact_types, test_contact_type):
        """Only contact types specified in configuration should be synchronized."""
        # Create a mock configuration manager (not using actual DynamoDB for property tests)
        config_data = {
            "contact_types": configured_contact_types,
            "excluded_accounts": [],
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            # Create config manager and load configuration
            manager = DynamoDBConfigManager()
            manager.load_config(config_data)
            
            # Test contact type filtering
            should_sync = manager.should_sync_contact_type(test_contact_type)
            expected_sync = test_contact_type in configured_contact_types
            
            assert should_sync == expected_sync, (
                f"Contact type '{test_contact_type}' sync decision should be {expected_sync} "
                f"when configured types are {configured_contact_types}"
            )

    @given(
        excluded_accounts=account_ids,
        test_account=st.text(alphabet="0123456789", min_size=12, max_size=12)
    )
    def test_account_exclusion_filtering_respects_configuration(self, excluded_accounts, test_account):
        """Accounts in the exclusion list should be skipped entirely."""
        config_data = {
            "contact_types": ["primary"],
            "excluded_accounts": excluded_accounts,
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            # Create config manager and load configuration
            manager = DynamoDBConfigManager()
            manager.load_config(config_data)
            
            # Test account exclusion filtering
            is_excluded = manager.is_account_excluded(test_account)
            expected_exclusion = test_account in excluded_accounts
            
            assert is_excluded == expected_exclusion, (
                f"Account '{test_account}' exclusion should be {expected_exclusion} "
                f"when excluded accounts are {excluded_accounts}"
            )

    @given(
        configured_contact_types=contact_type_subsets,
        excluded_accounts=account_ids,
        test_operations=st.lists(
            st.tuples(
                st.sampled_from(all_contact_types),  # contact_type
                st.text(alphabet="0123456789", min_size=12, max_size=12)  # account_id
            ),
            min_size=1,
            max_size=10
        )
    )
    def test_combined_filtering_logic(self, configured_contact_types, excluded_accounts, test_operations):
        """Combined contact type and account filtering should work correctly."""
        config_data = {
            "contact_types": configured_contact_types,
            "excluded_accounts": excluded_accounts,
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            # Create config manager and load configuration
            manager = DynamoDBConfigManager()
            manager.load_config(config_data)
            
            for contact_type, account_id in test_operations:
                should_sync_contact = manager.should_sync_contact_type(contact_type)
                is_account_excluded = manager.is_account_excluded(account_id)
                
                # An operation should proceed only if:
                # 1. The contact type is configured for sync AND
                # 2. The account is not excluded
                should_proceed = should_sync_contact and not is_account_excluded
                
                expected_contact_sync = contact_type in configured_contact_types
                expected_account_exclusion = account_id in excluded_accounts
                expected_proceed = expected_contact_sync and not expected_account_exclusion
                
                assert should_sync_contact == expected_contact_sync
                assert is_account_excluded == expected_account_exclusion
                assert should_proceed == expected_proceed, (
                    f"Operation for contact_type='{contact_type}', account_id='{account_id}' "
                    f"should proceed={expected_proceed}, but got {should_proceed}"
                )

    @given(contact_types=contact_type_subsets)
    def test_get_contact_type_filter_returns_configured_types(self, contact_types):
        """get_contact_type_filter should return exactly the configured contact types."""
        config_data = {
            "contact_types": contact_types,
            "excluded_accounts": [],
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            manager = DynamoDBConfigManager()
            manager.load_config(config_data)
            
            returned_types = manager.get_contact_type_filter()
            
            # Should return exactly the configured contact types
            assert set(returned_types) == set(contact_types)
            assert len(returned_types) == len(contact_types)

    @given(excluded_accounts=account_ids)
    def test_get_excluded_accounts_returns_configured_accounts(self, excluded_accounts):
        """get_excluded_accounts should return exactly the configured excluded accounts."""
        config_data = {
            "contact_types": ["primary"],
            "excluded_accounts": excluded_accounts,
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            manager = DynamoDBConfigManager()
            manager.load_config(config_data)
            
            returned_accounts = manager.get_excluded_accounts()
            
            # Should return exactly the configured excluded accounts
            assert set(returned_accounts) == set(excluded_accounts)
            assert len(returned_accounts) == len(excluded_accounts)

    def test_filtering_with_no_configuration_defaults_to_permissive(self):
        """When no configuration is loaded, filtering should default to allowing all operations."""
        with patch.object(DynamoDBConfigManager, '_get_table'):
            manager = DynamoDBConfigManager()
            
            # Mock read_config to return None (no configuration found)
            with patch.object(manager, 'read_config', return_value=None):
                # No configuration loaded - should default to permissive behavior
                assert manager.should_sync_contact_type("primary") is True
                assert manager.should_sync_contact_type("billing") is True
                assert manager.should_sync_contact_type("operations") is True
                assert manager.should_sync_contact_type("security") is True
                
                assert manager.is_account_excluded("123456789012") is False
                assert manager.is_account_excluded("987654321098") is False

    @given(
        initial_contact_types=contact_type_subsets,
        initial_excluded_accounts=account_ids,
        updated_contact_types=contact_type_subsets,
        updated_excluded_accounts=account_ids
    )
    def test_filtering_updates_when_configuration_changes(
        self, 
        initial_contact_types, 
        initial_excluded_accounts,
        updated_contact_types,
        updated_excluded_accounts
    ):
        """Filtering behavior should update when configuration changes."""
        # Initial configuration
        initial_config = {
            "contact_types": initial_contact_types,
            "excluded_accounts": initial_excluded_accounts,
            "retry_config": {"max_attempts": 3, "base_delay": 2, "max_delay": 60},
            "notification_settings": {"notify_on_failure": True, "failure_threshold": 1}
        }
        
        with patch.object(DynamoDBConfigManager, '_get_table'):
            manager = DynamoDBConfigManager()
            
            # Mock the DynamoDB operations for load_config
            with patch.object(manager, 'read_config', return_value=None):
                manager.load_config(initial_config)
            
            # Verify initial filtering behavior
            initial_filter = set(manager.get_contact_type_filter())
            initial_excluded = set(manager.get_excluded_accounts())
            
            assert initial_filter == set(initial_contact_types)
            assert initial_excluded == set(initial_excluded_accounts)
            
            # Update configuration (mock the update operation)
            updates = {
                "contact_types": updated_contact_types,
                "excluded_accounts": updated_excluded_accounts
            }
            
            # Mock update_config to simulate the update without DynamoDB
            updated_config_data = initial_config.copy()
            updated_config_data.update(updates)
            updated_config = manager.load_config(updated_config_data)
            
            # Verify updated filtering behavior
            updated_filter = set(manager.get_contact_type_filter())
            updated_excluded = set(manager.get_excluded_accounts())
            
            assert updated_filter == set(updated_contact_types)
            assert updated_excluded == set(updated_excluded_accounts)
            
            # Verify the configuration object reflects the changes
            assert set(updated_config.contact_types) == set(updated_contact_types)
            assert set(updated_config.excluded_accounts) == set(updated_excluded_accounts)
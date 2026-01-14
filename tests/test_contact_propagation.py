"""Property-based tests for contact information propagation consistency.

Feature: aws-contact-sync, Property 2: Contact Information Propagation Consistency
Validates: Requirements 2.1, 2.2, 2.3
"""

import pytest
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
from hypothesis import given, strategies as st, settings, assume
from typing import Dict, Any, List

from src.lambda_handlers.contact_sync_handler import ContactSyncHandler
from src.events.cloudtrail_parser import ContactChangeEvent
from src.models.contact_models import ContactInformation, AlternateContact
from src.models.sync_models import SyncOperation, AccountSyncResult
from src.aws_clients.organizations import OrganizationAccount


class TestContactPropagationProperties:
    """Property-based tests for contact information propagation consistency."""
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        num_member_accounts=st.integers(min_value=1, max_value=10),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        excluded_accounts_ratio=st.floats(min_value=0.0, max_value=0.5)  # Exclude up to 50% of accounts
    )
    @settings(max_examples=100, deadline=None)
    def test_contact_information_propagation_consistency(
        self, 
        management_account_id, 
        num_member_accounts, 
        contact_type,
        excluded_accounts_ratio
    ):
        """Property 2: For any detected contact change in the management account, 
        the same contact information should be successfully applied to all non-excluded 
        member accounts, preserving contact type and field values.
        
        **Validates: Requirements 2.1, 2.2, 2.3**
        """
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        assume(num_member_accounts >= 1)
        
        # Generate member accounts
        member_accounts = self._generate_member_accounts(num_member_accounts)
        member_account_ids = [acc.account_id for acc in member_accounts]
        
        # Determine excluded accounts
        num_excluded = int(num_member_accounts * excluded_accounts_ratio)
        excluded_account_ids = member_account_ids[:num_excluded] if num_excluded > 0 else []
        expected_target_accounts = [acc_id for acc_id in member_account_ids if acc_id not in excluded_account_ids]
        
        # Generate contact change event
        contact_event = self._generate_contact_change_event(management_account_id, contact_type)
        
        # Mock dependencies
        with patch('src.lambda_handlers.contact_sync_handler.OrganizationsClient') as mock_org_client, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBConfigManager') as mock_config_mgr, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBStateTracker') as mock_state_tracker, \
             patch('boto3.client') as mock_boto_client:
            
            # Setup Organizations client mock
            mock_org_instance = Mock()
            mock_org_client.return_value = mock_org_instance
            mock_org_instance.list_active_member_accounts.return_value = member_accounts
            
            # Setup Configuration manager mock
            mock_config_instance = Mock()
            mock_config_mgr.return_value = mock_config_instance
            mock_config_instance.should_sync_contact_type.return_value = True
            mock_config_instance.is_account_excluded.side_effect = lambda acc_id: acc_id in excluded_account_ids
            
            # Setup State tracker mock - return the sync operation as-is
            mock_state_instance = Mock()
            mock_state_tracker.return_value = mock_state_instance
            mock_state_instance.create_sync_operation.side_effect = lambda sync_op: sync_op
            mock_state_instance.update_sync_status.return_value = None
            
            # Setup Lambda client mock
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            
            # Initialize handler
            handler = ContactSyncHandler(
                management_account_id=management_account_id,
                config_table_name="test-config",
                state_table_name="test-state",
                account_processor_function_name="test-processor"
            )
            
            # Process the contact change
            result = handler.process_contact_change(contact_event)
            
            # Verify the result indicates successful initiation
            assert result['status'] == 'initiated'
            assert result['event_id'] == contact_event.event_id
            assert 'sync_id' in result
            assert result['target_accounts'] == len(expected_target_accounts)
            
            # Verify Organizations client was called correctly
            mock_org_instance.list_active_member_accounts.assert_called_once_with(exclude_management_account=True)
            
            # Verify configuration filtering was applied
            for account_id in member_account_ids:
                mock_config_instance.is_account_excluded.assert_any_call(account_id)
            
            # Verify contact type filtering was applied
            mock_config_instance.should_sync_contact_type.assert_called_once_with(contact_type)
            
            # Verify sync operation was created
            mock_state_instance.create_sync_operation.assert_called_once()
            created_sync_op = mock_state_instance.create_sync_operation.call_args[0][0]
            
            # Verify sync operation properties
            assert isinstance(created_sync_op, SyncOperation)
            assert created_sync_op.contact_type == contact_type
            assert created_sync_op.source_account == management_account_id
            assert created_sync_op.initiating_user == contact_event.initiating_user
            assert set(created_sync_op.target_accounts) == set(expected_target_accounts)
            assert created_sync_op.status == "pending"
            
            # Verify contact data preservation
            if contact_type == "primary":
                assert isinstance(created_sync_op.contact_data, ContactInformation)
                assert created_sync_op.contact_data == contact_event.contact_data
            else:
                assert isinstance(created_sync_op.contact_data, AlternateContact)
                assert created_sync_op.contact_data == contact_event.contact_data
                assert created_sync_op.contact_data.contact_type == contact_type
            
            # Verify Lambda invocations for each target account
            assert mock_lambda_client.invoke.call_count == len(expected_target_accounts)
            
            # Verify each account processor invocation
            invoked_accounts = set()
            for call in mock_lambda_client.invoke.call_args_list:
                args, kwargs = call
                assert kwargs['FunctionName'] == "test-processor"
                assert kwargs['InvocationType'] == 'Event'  # Asynchronous
                
                payload = json.loads(kwargs['Payload'])
                assert payload['sync_id'] == created_sync_op.sync_id
                assert payload['contact_type'] == contact_type
                assert payload['initiating_user'] == contact_event.initiating_user
                
                account_id = payload['account_id']
                assert account_id in expected_target_accounts
                assert account_id not in excluded_account_ids
                invoked_accounts.add(account_id)
                
                # Verify contact data serialization
                contact_data = payload['contact_data']
                if contact_type == "primary":
                    assert 'full_name' in contact_data
                    assert 'address_line1' in contact_data
                    assert 'city' in contact_data
                    assert 'country_code' in contact_data
                    assert 'phone_number' in contact_data
                    assert 'postal_code' in contact_data
                else:
                    assert 'name' in contact_data
                    assert 'email_address' in contact_data
                    assert 'phone_number' in contact_data
                    assert 'title' in contact_data
                    assert 'contact_type' in contact_data
                    assert contact_data['contact_type'] == contact_type
            
            # Verify all expected accounts were invoked
            assert invoked_accounts == set(expected_target_accounts)
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        num_member_accounts=st.integers(min_value=2, max_value=8),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        sync_all_contact_types=st.booleans()
    )
    @settings(max_examples=100, deadline=None)
    def test_contact_type_preservation_across_accounts(
        self, 
        management_account_id, 
        num_member_accounts, 
        contact_type,
        sync_all_contact_types
    ):
        """Property: For any contact change, the contact type should be preserved 
        consistently across all member accounts during propagation.
        
        **Validates: Requirements 2.3**
        """
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        assume(num_member_accounts >= 2)
        
        # Generate member accounts
        member_accounts = self._generate_member_accounts(num_member_accounts)
        
        # Generate contact change event
        contact_event = self._generate_contact_change_event(management_account_id, contact_type)
        
        # Mock dependencies
        with patch('src.lambda_handlers.contact_sync_handler.OrganizationsClient') as mock_org_client, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBConfigManager') as mock_config_mgr, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBStateTracker') as mock_state_tracker, \
             patch('boto3.client') as mock_boto_client:
            
            # Setup mocks
            mock_org_instance = Mock()
            mock_org_client.return_value = mock_org_instance
            mock_org_instance.list_active_member_accounts.return_value = member_accounts
            
            mock_config_instance = Mock()
            mock_config_mgr.return_value = mock_config_instance
            mock_config_instance.should_sync_contact_type.return_value = sync_all_contact_types
            mock_config_instance.is_account_excluded.return_value = False  # No exclusions
            
            # Setup State tracker mock - return the sync operation as-is
            mock_state_instance = Mock()
            mock_state_tracker.return_value = mock_state_instance
            mock_state_instance.create_sync_operation.side_effect = lambda sync_op: sync_op
            mock_state_instance.update_sync_status.return_value = None
            
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            
            # Initialize handler
            handler = ContactSyncHandler(management_account_id=management_account_id)
            
            # Process the contact change
            result = handler.process_contact_change(contact_event)
            
            if sync_all_contact_types:
                # Should process the contact change
                assert result['status'] == 'initiated'
                
                # Verify all Lambda invocations have consistent contact type
                for call in mock_lambda_client.invoke.call_args_list:
                    payload = json.loads(call[1]['Payload'])
                    assert payload['contact_type'] == contact_type
                    
                    # Verify contact data type consistency
                    contact_data = payload['contact_data']
                    if contact_type == "primary":
                        # Primary contact should not have contact_type field
                        assert 'contact_type' not in contact_data or contact_data.get('contact_type') is None
                    else:
                        # Alternate contact should have matching contact_type
                        assert contact_data['contact_type'] == contact_type
            else:
                # Should skip the contact change
                assert result['status'] == 'skipped'
                assert 'not configured for sync' in result['reason']
                
                # No Lambda invocations should occur
                mock_lambda_client.invoke.assert_not_called()
    
    @given(
        management_account_id=st.text(min_size=12, max_size=12, alphabet=st.characters(whitelist_categories=('Nd',))),
        num_member_accounts=st.integers(min_value=1, max_value=5),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"])
    )
    @settings(max_examples=100, deadline=None)
    def test_empty_member_account_list_handling(
        self, 
        management_account_id, 
        num_member_accounts, 
        contact_type
    ):
        """Property: For any contact change when no member accounts exist or all are excluded,
        the system should complete successfully without attempting synchronization.
        
        **Validates: Requirements 2.1, 2.2**
        """
        assume(len(management_account_id) == 12)
        assume(management_account_id.isdigit())
        
        # Generate contact change event
        contact_event = self._generate_contact_change_event(management_account_id, contact_type)
        
        # Test with empty member account list
        with patch('src.lambda_handlers.contact_sync_handler.OrganizationsClient') as mock_org_client, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBConfigManager') as mock_config_mgr, \
             patch('src.lambda_handlers.contact_sync_handler.DynamoDBStateTracker') as mock_state_tracker, \
             patch('boto3.client') as mock_boto_client:
            
            # Setup mocks for empty account list
            mock_org_instance = Mock()
            mock_org_client.return_value = mock_org_instance
            mock_org_instance.list_active_member_accounts.return_value = []  # No member accounts
            
            mock_config_instance = Mock()
            mock_config_mgr.return_value = mock_config_instance
            mock_config_instance.should_sync_contact_type.return_value = True
            
            mock_state_instance = Mock()
            mock_state_tracker.return_value = mock_state_instance
            
            mock_lambda_client = Mock()
            mock_boto_client.return_value = mock_lambda_client
            
            # Initialize handler
            handler = ContactSyncHandler(management_account_id=management_account_id)
            
            # Process the contact change
            result = handler.process_contact_change(contact_event)
            
            # Should complete successfully with no synchronization
            assert result['status'] == 'completed'
            assert 'No member accounts' in result['message']
            
            # No Lambda invocations should occur
            mock_lambda_client.invoke.assert_not_called()
            
            # No sync operation should be created
            mock_state_instance.create_sync_operation.assert_not_called()
        
        # Test with all accounts excluded
        if num_member_accounts > 0:
            member_accounts = self._generate_member_accounts(num_member_accounts)
            member_account_ids = [acc.account_id for acc in member_accounts]
            
            with patch('src.lambda_handlers.contact_sync_handler.OrganizationsClient') as mock_org_client, \
                 patch('src.lambda_handlers.contact_sync_handler.DynamoDBConfigManager') as mock_config_mgr, \
                 patch('src.lambda_handlers.contact_sync_handler.DynamoDBStateTracker') as mock_state_tracker, \
                 patch('boto3.client') as mock_boto_client:
                
                # Setup mocks with all accounts excluded
                mock_org_instance = Mock()
                mock_org_client.return_value = mock_org_instance
                mock_org_instance.list_active_member_accounts.return_value = member_accounts
                
                mock_config_instance = Mock()
                mock_config_mgr.return_value = mock_config_instance
                mock_config_instance.should_sync_contact_type.return_value = True
                mock_config_instance.is_account_excluded.return_value = True  # All accounts excluded
                
                mock_state_instance = Mock()
                mock_state_tracker.return_value = mock_state_instance
                
                mock_lambda_client = Mock()
                mock_boto_client.return_value = mock_lambda_client
                
                # Initialize handler
                handler = ContactSyncHandler(management_account_id=management_account_id)
                
                # Process the contact change
                result = handler.process_contact_change(contact_event)
                
                # Should complete successfully with no synchronization
                assert result['status'] == 'completed'
                assert 'filtered out by configuration' in result['message']
                
                # No Lambda invocations should occur
                mock_lambda_client.invoke.assert_not_called()
                
                # No sync operation should be created
                mock_state_instance.create_sync_operation.assert_not_called()
    
    def _generate_member_accounts(self, num_accounts: int) -> List[OrganizationAccount]:
        """Generate a list of member accounts for testing."""
        accounts = []
        for i in range(num_accounts):
            account_id = f"{100000000000 + i + 1:012d}"  # Generate 12-digit account IDs
            accounts.append(OrganizationAccount(
                account_id=account_id,
                name=f"Member Account {i+1}",
                email=f"member{i+1}@example.com",
                status="ACTIVE",
                joined_method="INVITED",
                joined_timestamp=datetime.now(timezone.utc).isoformat()
            ))
        return accounts
    
    def _generate_contact_change_event(self, management_account_id: str, contact_type: str) -> ContactChangeEvent:
        """Generate a contact change event for testing."""
        event_id = str(uuid.uuid4())
        event_time = datetime.now(timezone.utc)
        initiating_user = "arn:aws:iam::123456789012:user/test-user"
        
        if contact_type == "primary":
            event_name = "PutContactInformation"
            contact_data = ContactInformation(
                address_line1="123 Test Street",
                city="Test City",
                country_code="US",
                full_name="Test User",
                phone_number="+1-555-123-4567",
                postal_code="12345",
                company_name="Test Company",
                state_or_region="CA"
            )
        else:
            event_name = "PutAlternateContact"
            contact_data = AlternateContact(
                contact_type=contact_type,
                email_address="test@example.com",
                name="Test Contact",
                phone_number="+1-555-987-6543",
                title="Test Title"
            )
        
        return ContactChangeEvent(
            event_id=event_id,
            event_time=event_time,
            event_name=event_name,
            initiating_user=initiating_user,
            source_account=management_account_id,
            contact_type=contact_type,
            contact_data=contact_data,
            is_management_account_change=True
        )
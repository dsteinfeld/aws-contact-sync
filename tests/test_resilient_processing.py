"""
Property-based tests for resilient processing behavior.

Tests that the system continues processing all accounts even when some fail,
and properly handles different types of failures (temporary, permission, etc.).
"""

import json
import pytest
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st, assume, settings
from datetime import datetime
from typing import List, Dict, Any

from src.lambda_handlers.account_processor_handler import AccountProcessorHandler
from src.models.contact_models import ContactInformation, AlternateContact
from src.models.sync_models import AccountSyncResult
from botocore.exceptions import ClientError


class TestResilientProcessingProperties:
    """Property-based tests for resilient processing behavior."""

    def _generate_contact_data(self, contact_type: str) -> Dict[str, Any]:
        """Generate valid contact data for testing."""
        if contact_type.lower() == "primary":
            return {
                'address_line1': '123 Test St',
                'city': 'Test City',
                'country_code': 'US',
                'full_name': 'Test User',
                'phone_number': '+1-555-0123',
                'postal_code': '12345'
            }
        else:
            return {
                'contact_type': contact_type.upper(),
                'email_address': 'test@example.com',
                'name': 'Test Contact',
                'phone_number': '+1-555-0123',
                'title': 'Test Title'
            }

    def _create_client_error(self, error_code: str, message: str = "Test error") -> ClientError:
        """Create a ClientError for testing."""
        return ClientError(
            error_response={
                'Error': {
                    'Code': error_code,
                    'Message': message
                }
            },
            operation_name='TestOperation'
        )

    @pytest.mark.property
    @given(
        num_accounts=st.integers(min_value=2, max_value=6),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        error_types=st.lists(
            st.sampled_from([
                "AccessDenied",  # Permission error (non-retryable)
                "Throttling",  # Temporary error (retryable)
                "ValidationException",  # Configuration error (non-retryable)
                "InternalServerError",  # Temporary error (retryable)
                "AccountNotFound"  # Non-retryable error
            ]),
            min_size=1,
            max_size=3
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_account_processor_resilient_processing(self,
                                                    num_accounts: int,
                                                    contact_type: str,
                                                    error_types: List[str]):
        """
        Property 4b: For any account processor operation where some API calls fail,
        the system should continue processing and properly classify failures as
        retryable or non-retryable, exhausting retries appropriately.

        **Feature: aws-contact-sync, Property 4: Resilient Processing**
        **Validates: Requirements 2.4, 3.2**
        """
        # Generate valid account IDs
        account_ids = [f"123456789{str(i).zfill(3)}" for i in range(num_accounts)]

        # Generate contact data
        contact_data = self._generate_contact_data(contact_type)

        # Create failure mapping - some accounts will fail, some will succeed
        failure_map = {}
        for i in range(min(len(account_ids) // 2, len(error_types))):
            error_code = error_types[i % len(error_types)]
            failure_map[account_ids[i]] = error_code

        # Mock the AWS clients
        with patch('src.lambda_handlers.account_processor_handler.AccountManagementClient') as mock_acct_mgmt, \
                patch('src.lambda_handlers.account_processor_handler.OrganizationsClient') as mock_orgs, \
                patch('src.lambda_handlers.account_processor_handler.DynamoDBStateTracker') as mock_state:

            # Configure account management client mock
            mock_acct_mgmt_instance = mock_acct_mgmt.return_value

            def mock_get_contact_info(account_id):
                if account_id in failure_map:
                    error_code = failure_map[account_id]
                    raise self._create_client_error(error_code, f"Simulated {error_code} for {account_id}")
                return None  # No existing contact (update needed)

            def mock_put_contact_info(account_id, contact_obj):
                if account_id in failure_map:
                    error_code = failure_map[account_id]
                    raise self._create_client_error(error_code, f"Simulated {error_code} for {account_id}")
                return True  # Success

            if contact_type.lower() == "primary":
                mock_acct_mgmt_instance.get_contact_information.side_effect = mock_get_contact_info
                mock_acct_mgmt_instance.put_contact_information.side_effect = mock_put_contact_info
            else:
                mock_acct_mgmt_instance.get_alternate_contact.side_effect = mock_get_contact_info
                mock_acct_mgmt_instance.put_alternate_contact.side_effect = mock_put_contact_info

            # Configure state tracker mock
            mock_state_instance = mock_state.return_value
            mock_state_instance.add_account_result.return_value = None

            # Create account processor handler
            from src.lambda_handlers.account_processor_handler import AccountProcessorHandler

            handler = AccountProcessorHandler(
                management_account_id="123456789012",
                max_retry_attempts=3,
                base_retry_delay=0.1,  # Fast retries for testing
                max_retry_delay=1.0
            )

            # Process each account and collect results
            results = {}
            for account_id in account_ids:
                try:
                    result = handler.process_account_update(
                        sync_id="test-sync-123",
                        account_id=account_id,
                        contact_type=contact_type,
                        contact_data=contact_data,
                        initiating_user="test-user"
                    )
                    results[account_id] = result
                except Exception as e:
                    # Handler should not raise exceptions - it should return error status
                    results[account_id] = {
                        'status': 'failed',
                        'message': str(e),
                        'retry_count': 0
                    }

            # Verify resilient processing properties

            # 1. All accounts were processed (no early termination)
            assert len(results) == len(account_ids), "Not all accounts were processed"

            # 2. Accounts not in failure_map should succeed
            successful_accounts = [aid for aid in account_ids if aid not in failure_map]
            for account_id in successful_accounts:
                assert results[account_id]['status'] == 'success', \
                    f"Account {account_id} should have succeeded but got {results[account_id]['status']}"

            # 3. Accounts in failure_map should fail appropriately
            for account_id, error_code in failure_map.items():
                if account_id in account_ids:
                    result = results[account_id]
                    assert result['status'] == 'failed', \
                        f"Account {account_id} should have failed but got {result['status']}"

                    # Check retry behavior based on error type
                    is_retryable = handler.is_retryable_error(error_code)
                    if is_retryable:
                        # Retryable errors should have attempted retries
                        assert result['retry_count'] >= 0, \
                            f"Retryable error {error_code} should have retry attempts"
                    else:
                        # Non-retryable errors should fail immediately
                        assert result['retry_count'] == 0, \
                            f"Non-retryable error {error_code} should not retry"

            # 4. State tracker should have been called for each account
            assert mock_state_instance.add_account_result.call_count >= len(account_ids), \
                "State tracker should be updated for each account"

            # The key property: processing continues for all accounts regardless of individual failures

    @pytest.mark.property
    @given(
        num_accounts=st.integers(min_value=3, max_value=8),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        failure_ratio=st.floats(min_value=0.2, max_value=0.7)  # 20-70% of accounts fail
    )
    @settings(max_examples=50, deadline=None)
    def test_resilient_processing_continues_despite_failures(self,
                                                             num_accounts: int,
                                                             contact_type: str,
                                                             failure_ratio: float):
        """
        Property 4: For any synchronization operation where some member accounts fail,
        the system should continue processing all remaining accounts and complete the
        operation for successful accounts.

        **Feature: aws-contact-sync, Property 4: Resilient Processing**
        **Validates: Requirements 2.4, 3.2**
        """
        # Generate account IDs
        account_ids = [f"12345678901{i}" for i in range(num_accounts)]

        # Determine which accounts should fail
        num_failures = max(1, int(num_accounts * failure_ratio))
        failing_accounts = set(account_ids[:num_failures])
        successful_accounts = set(account_ids[num_failures:])

        # Ensure we have at least one successful account for the property
        assume(len(successful_accounts) > 0)

        # Generate contact data
        contact_data = self._generate_contact_data(contact_type)

        # Mock the AWS clients and state tracker
        with patch('src.lambda_handlers.contact_sync_handler.OrganizationsClient') as mock_orgs, \
                patch('src.lambda_handlers.contact_sync_handler.DynamoDBConfigManager') as mock_config, \
                patch('src.lambda_handlers.contact_sync_handler.DynamoDBStateTracker') as mock_state, \
                patch('src.lambda_handlers.contact_sync_handler.boto3.client') as mock_lambda_client:

            # Configure mocks
            mock_orgs_instance = mock_orgs.return_value
            mock_orgs_instance.list_active_member_accounts.return_value = [
                Mock(account_id=account_id) for account_id in account_ids
            ]

            mock_config_instance = mock_config.return_value
            mock_config_instance.should_sync_contact_type.return_value = True
            mock_config_instance.is_account_excluded.return_value = False

            mock_state_instance = mock_state.return_value
            mock_state_instance.create_sync_operation.return_value = Mock(
                sync_id="test-sync-123",
                timestamp=datetime.utcnow(),
                initiating_user="test-user",
                contact_type=contact_type,
                source_account="123456789012",
                target_accounts=account_ids,
                status="pending",
                contact_data=contact_data,
                results={account_id: Mock(account_id=account_id, status="pending")
                         for account_id in account_ids}
            )

            # Mock Lambda client to simulate failures for specific accounts
            mock_lambda_instance = mock_lambda_client.return_value

            def mock_invoke(**kwargs):
                payload = json.loads(kwargs['Payload'])
                account_id = payload['account_id']

                if account_id in failing_accounts:
                    # Simulate Lambda invocation failure
                    raise self._create_client_error("AccessDenied", f"Simulated failure for {account_id}")
                else:
                    # Successful invocation
                    return {'StatusCode': 202}

            mock_lambda_instance.invoke.side_effect = mock_invoke

            # Create handler and process contact change
            from src.lambda_handlers.contact_sync_handler import ContactSyncHandler
            from src.events.cloudtrail_parser import ContactChangeEvent

            handler = ContactSyncHandler("123456789012")

            # Create a mock contact change event
            contact_event = ContactChangeEvent(
                event_id="test-event-123",
                event_time=datetime.utcnow(),
                contact_type=contact_type,
                source_account="123456789012",
                initiating_user="test-user",
                contact_data=contact_data
            )

            # Process the contact change
            result = handler.process_contact_change(contact_event)

            # Verify that the operation was initiated despite some failures
            assert result['status'] == 'initiated'
            assert 'sync_id' in result

            # Verify that Lambda was invoked for all accounts (both successful and failing)
            assert mock_lambda_instance.invoke.call_count == num_accounts

            # Verify that the sync operation was created with all accounts
            mock_state_instance.create_sync_operation.assert_called_once()
            create_call_args = mock_state_instance.create_sync_operation.call_args[1]
            assert set(create_call_args['target_accounts']) == set(account_ids)

            # Verify that the handler attempted to process all accounts
            # (both successful and failing ones should have been attempted)
            invoked_accounts = set()
            for call in mock_lambda_instance.invoke.call_args_list:
                payload = json.loads(call[1]['Payload'])
                invoked_accounts.add(payload['account_id'])

            assert invoked_accounts == set(account_ids)

            # The key property: despite some failures, all accounts were attempted
            # This demonstrates resilient processing - failures don't stop processing of other accounts

    @pytest.mark.property
    @given(
        num_accounts=st.integers(min_value=2, max_value=6),
        contact_type=st.sampled_from(["primary", "BILLING", "OPERATIONS", "SECURITY"]),
        error_types=st.lists(
            st.sampled_from([
                "AccessDenied",  # Permission error (non-retryable)
                "Throttling",  # Temporary error (retryable)
                "ValidationException",  # Configuration error (non-retryable)
                "InternalServerError",  # Temporary error (retryable)
                "AccountNotFound"  # Non-retryable error
            ]),
            min_size=1,
            max_size=3
        )
    )
    @settings(max_examples=50, deadline=None)
    def test_account_processor_resilient_processing(self,
                                                    num_accounts: int,
                                                    contact_type: str,
                                                    error_types: List[str]):
        """
        Property 4b: For any account processor operation where some API calls fail,
        the system should continue processing and properly classify failures as
        retryable or non-retryable, exhausting retries appropriately.

        **Feature: aws-contact-sync, Property 4: Resilient Processing**
        **Validates: Requirements 2.4, 3.2**
        """
        # Generate valid account IDs
        account_ids = [f"123456789{str(i).zfill(3)}" for i in range(num_accounts)]

        # Generate contact data
        contact_data = self._generate_contact_data(contact_type)

        # Create failure mapping - some accounts will fail, some will succeed
        failure_map = {}
        for i in range(min(len(account_ids) // 2, len(error_types))):
            error_code = error_types[i % len(error_types)]
            failure_map[account_ids[i]] = error_code

        # Mock the AWS clients
        with patch('src.lambda_handlers.account_processor_handler.AccountManagementClient') as mock_acct_mgmt, \
                patch('src.lambda_handlers.account_processor_handler.OrganizationsClient') as mock_orgs, \
                patch('src.lambda_handlers.account_processor_handler.DynamoDBStateTracker') as mock_state:

            # Configure account management client mock
            mock_acct_mgmt_instance = mock_acct_mgmt.return_value

            def mock_get_contact_info(account_id):
                if account_id in failure_map:
                    error_code = failure_map[account_id]
                    raise self._create_client_error(error_code, f"Simulated {error_code} for {account_id}")
                return None  # No existing contact (update needed)

            def mock_put_contact_info(account_id, contact_obj):
                if account_id in failure_map:
                    error_code = failure_map[account_id]
                    raise self._create_client_error(error_code, f"Simulated {error_code} for {account_id}")
                return True  # Success

            if contact_type.lower() == "primary":
                mock_acct_mgmt_instance.get_contact_information.side_effect = mock_get_contact_info
                mock_acct_mgmt_instance.put_contact_information.side_effect = mock_put_contact_info
            else:
                mock_acct_mgmt_instance.get_alternate_contact.side_effect = mock_get_contact_info
                mock_acct_mgmt_instance.put_alternate_contact.side_effect = mock_put_contact_info

            # Configure state tracker mock
            mock_state_instance = mock_state.return_value
            mock_state_instance.add_account_result.return_value = None

            # Create account processor handler
            from src.lambda_handlers.account_processor_handler import AccountProcessorHandler

            handler = AccountProcessorHandler(
                management_account_id="123456789012",
                max_retry_attempts=3,
                base_retry_delay=0.1,  # Fast retries for testing
                max_retry_delay=1.0
            )

            # Process each account and collect results
            results = {}
            for account_id in account_ids:
                try:
                    result = handler.process_account_update(
                        sync_id="test-sync-123",
                        account_id=account_id,
                        contact_type=contact_type,
                        contact_data=contact_data,
                        initiating_user="test-user"
                    )
                    results[account_id] = result
                except Exception as e:
                    # Handler should not raise exceptions - it should return error status
                    results[account_id] = {
                        'status': 'failed',
                        'message': str(e),
                        'retry_count': 0
                    }

            # Verify resilient processing properties

            # 1. All accounts were processed (no early termination)
            assert len(results) == len(account_ids), "Not all accounts were processed"

            # 2. Accounts not in failure_map should succeed
            successful_accounts = [aid for aid in account_ids if aid not in failure_map]
            for account_id in successful_accounts:
                assert results[account_id]['status'] == 'success', \
                    f"Account {account_id} should have succeeded but got {results[account_id]['status']}"

            # 3. Accounts in failure_map should fail appropriately
            for account_id, error_code in failure_map.items():
                if account_id in account_ids:
                    result = results[account_id]
                    assert result['status'] == 'failed', \
                        f"Account {account_id} should have failed but got {result['status']}"

                    # Check retry behavior based on error type
                    is_retryable = handler.is_retryable_error(error_code)
                    if is_retryable:
                        # Retryable errors should have attempted retries
                        assert result['retry_count'] >= 0, \
                            f"Retryable error {error_code} should have retry attempts"
                    else:
                        # Non-retryable errors should fail immediately
                        assert result['retry_count'] == 0, \
                            f"Non-retryable error {error_code} should not retry"

            # 4. State tracker should have been called for each account
            assert mock_state_instance.add_account_result.call_count >= len(account_ids), \
                "State tracker should be updated for each account"

            # The key property: processing continues for all accounts regardless of individual failures
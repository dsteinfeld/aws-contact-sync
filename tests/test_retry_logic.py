"""Property-based tests for retry logic with exponential backoff.

Feature: aws-contact-sync, Property 3: Retry Logic with Exponential Backoff
Validates: Requirements 3.1
"""

import pytest
import time
from unittest.mock import Mock, patch
from hypothesis import given, strategies as st, settings
from botocore.exceptions import ClientError

from src.aws_clients.account_management import AccountManagementClient
from src.config.config_manager import RetryConfig
from src.models.contact_models import ContactInformation


class TestRetryLogicProperties:
    """Property-based tests for retry logic with exponential backoff."""
    
    @given(
        max_attempts=st.integers(min_value=1, max_value=5),
        base_delay=st.integers(min_value=1, max_value=10),
        max_delay=st.integers(min_value=10, max_value=60)
    )
    @settings(max_examples=100)
    def test_retry_logic_with_exponential_backoff(self, max_attempts, base_delay, max_delay):
        """Property 3: For any member account that experiences temporary failures, 
        the system should retry updates exactly 3 times with exponential backoff delays 
        (2s, 4s, 8s) before marking the account as failed.
        
        **Validates: Requirements 3.1**
        """
        # Ensure max_delay is at least base_delay
        if max_delay < base_delay:
            max_delay = base_delay * 4
        
        retry_config = RetryConfig(
            max_attempts=max_attempts,
            base_delay=base_delay,
            max_delay=max_delay
        )
        
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Configure mock to always raise a retryable error
        retryable_error = ClientError(
            error_response={'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}},
            operation_name='GetContactInformation'
        )
        mock_client.get_contact_information.side_effect = retryable_error
        
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        # Track timing to verify exponential backoff
        start_time = time.time()
        
        with pytest.raises(ClientError):
            client.get_contact_information()
        
        end_time = time.time()
        total_time = end_time - start_time
        
        # Verify the correct number of attempts were made
        assert mock_client.get_contact_information.call_count == max_attempts
        
        # Verify exponential backoff timing (with some tolerance for execution overhead)
        if max_attempts > 1:
            # Calculate expected minimum delay time
            expected_min_delay = 0
            for attempt in range(max_attempts - 1):  # -1 because last attempt doesn't have delay
                delay = min(base_delay * (2 ** attempt), max_delay)
                expected_min_delay += delay * 0.9  # 90% of expected delay (accounting for jitter)
            
            # Total time should be at least the expected delay time
            assert total_time >= expected_min_delay, f"Expected at least {expected_min_delay}s, got {total_time}s"
    
    @given(
        error_codes=st.lists(
            st.sampled_from(['ThrottlingException', 'ServiceUnavailableException', 'AccessDeniedException']),
            min_size=1,
            max_size=3
        )
    )
    @settings(max_examples=100)
    def test_retryable_vs_non_retryable_errors(self, error_codes):
        """Property: For any error type, retryable errors should be retried up to max_attempts,
        while non-retryable errors should fail immediately.
        
        **Validates: Requirements 3.1**
        """
        retry_config = RetryConfig(max_attempts=3, base_delay=1, max_delay=10)
        
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        for error_code in error_codes:
            # Reset mock for each error code
            mock_client.reset_mock()
            
            error = ClientError(
                error_response={'Error': {'Code': error_code, 'Message': 'Test error'}},
                operation_name='GetContactInformation'
            )
            mock_client.get_contact_information.side_effect = error
            
            with pytest.raises(ClientError):
                client.get_contact_information()
            
            # Check if error is retryable based on our client's configuration
            is_retryable = error_code in ['ThrottlingException', 'ServiceUnavailableException', 'InternalServerError']
            
            if is_retryable:
                # Retryable errors should be attempted max_attempts times
                assert mock_client.get_contact_information.call_count == retry_config.max_attempts
            else:
                # Non-retryable errors should fail immediately
                assert mock_client.get_contact_information.call_count == 1
    
    @given(
        success_on_attempt=st.integers(min_value=1, max_value=3)
    )
    @settings(max_examples=100)
    def test_successful_retry_after_failures(self, success_on_attempt):
        """Property: For any operation that succeeds after N failures,
        the system should return success and stop retrying.
        
        **Validates: Requirements 3.1**
        """
        retry_config = RetryConfig(max_attempts=3, base_delay=1, max_delay=10)
        
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        # Configure mock to succeed on the specified attempt
        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < success_on_attempt:
                raise ClientError(
                    error_response={'Error': {'Code': 'ThrottlingException', 'Message': 'Rate exceeded'}},
                    operation_name='GetContactInformation'
                )
            else:
                return {
                    'ContactInformation': {
                        'AddressLine1': '123 Test St',
                        'City': 'Test City',
                        'CountryCode': 'US',
                        'FullName': 'Test User',
                        'PhoneNumber': '+1234567890',
                        'PostalCode': '12345'
                    }
                }
        
        mock_client.get_contact_information.side_effect = side_effect
        mock_session.client.return_value = mock_client
        
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        # Should succeed without raising an exception
        result = client.get_contact_information()
        
        # Verify we got a valid ContactInformation object
        assert isinstance(result, ContactInformation)
        assert result.full_name == 'Test User'
        
        # Verify the correct number of attempts were made
        assert mock_client.get_contact_information.call_count == success_on_attempt
    
    @given(
        base_delay=st.integers(min_value=1, max_value=5),
        max_delay=st.integers(min_value=10, max_value=30)
    )
    @settings(max_examples=100)
    def test_backoff_delay_calculation(self, base_delay, max_delay):
        """Property: For any base_delay and max_delay configuration,
        calculated backoff delays should follow exponential pattern and respect max_delay.
        
        **Validates: Requirements 3.1**
        """
        # Ensure max_delay is at least base_delay
        if max_delay < base_delay:
            max_delay = base_delay * 4
        
        retry_config = RetryConfig(max_attempts=3, base_delay=base_delay, max_delay=max_delay)
        
        # Create mock session and client
        mock_session = Mock()
        mock_client = Mock()
        mock_session.client.return_value = mock_client
        
        client = AccountManagementClient(retry_config=retry_config, session=mock_session)
        
        # Test delay calculation for different attempts
        for attempt in range(3):
            delay = client._calculate_backoff_delay(attempt)
            
            # Calculate expected delay without jitter
            expected_base = min(base_delay * (2 ** attempt), max_delay)
            
            # Delay should be within reasonable bounds (base + jitter)
            assert delay >= expected_base, f"Delay {delay} should be at least {expected_base}"
            assert delay <= expected_base * 1.5, f"Delay {delay} should not exceed {expected_base * 1.5}"
            
            # Delay should not exceed max_delay (plus reasonable jitter tolerance)
            assert delay <= max_delay * 1.5, f"Delay {delay} should not exceed max_delay {max_delay}"
"""
Unit tests for error handling and resilience components.

Tests error classification, circuit breaker functionality, and recovery mechanisms
to ensure proper error handling across various failure scenarios.
"""

import pytest
import time
from unittest.mock import Mock, patch, MagicMock
from botocore.exceptions import ClientError, BotoCoreError
from datetime import datetime

from src.error_handling.error_classifier import (
    ErrorClassifier, ErrorClassification, ErrorCategory, ErrorSeverity
)
from src.error_handling.circuit_breaker import (
    CircuitBreaker, CircuitBreakerConfig, CircuitState, CircuitBreakerError,
    CircuitBreakerManager
)
from src.error_handling.recovery_manager import (
    RecoveryManager, RecoveryConfig, RecoveryResult
)


class TestErrorClassifier:
    """Test error classification functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.classifier = ErrorClassifier()
    
    def test_classify_throttling_exception(self):
        """Test classification of throttling exceptions."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'ThrottlingException',
                    'Message': 'Request was throttled'
                }
            },
            operation_name='PutContactInformation'
        )
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.RATE_LIMIT
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is True
        assert classification.should_circuit_break is False
        assert classification.retry_delay_multiplier == 2.0
        assert classification.max_retry_attempts == 5
        assert classification.recovery_action == "exponential_backoff"
    
    def test_classify_access_denied_exception(self):
        """Test classification of access denied exceptions."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'AccessDeniedException',
                    'Message': 'User is not authorized'
                }
            },
            operation_name='PutContactInformation'
        )
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.PERMISSION
        assert classification.severity == ErrorSeverity.HIGH
        assert classification.is_retryable is False
        assert classification.should_circuit_break is False
        assert classification.recovery_action == "skip_account"
    
    def test_classify_service_unavailable_exception(self):
        """Test classification of service unavailable exceptions."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'ServiceUnavailableException',
                    'Message': 'Service is temporarily unavailable'
                }
            },
            operation_name='ListAccounts'
        )
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.TRANSIENT
        assert classification.severity == ErrorSeverity.HIGH
        assert classification.is_retryable is True
        assert classification.should_circuit_break is True
        assert classification.recovery_action == "circuit_breaker"
    
    def test_classify_validation_exception(self):
        """Test classification of validation exceptions."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'ValidationException',
                    'Message': 'Invalid parameter value'
                }
            },
            operation_name='PutContactInformation'
        )
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.CONFIGURATION
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is False
        assert classification.should_circuit_break is False
        assert classification.recovery_action == "log_and_skip"
    
    def test_classify_unknown_aws_error(self):
        """Test classification of unknown AWS error codes."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'UnknownErrorCode',
                    'Message': 'Unknown error occurred'
                }
            },
            operation_name='PutContactInformation'
        )
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.UNKNOWN
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is True
        assert classification.max_retry_attempts == 2
    
    def test_classify_botocore_error(self):
        """Test classification of BotoCoreError (network issues)."""
        error = BotoCoreError()
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.NETWORK
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is True
        assert classification.should_circuit_break is False
        assert classification.recovery_action == "retry"
    
    def test_classify_connection_error(self):
        """Test classification of connection errors."""
        error = ConnectionError("Connection failed")
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.NETWORK
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is True
        assert classification.max_retry_attempts == 3
    
    def test_classify_value_error(self):
        """Test classification of value errors."""
        error = ValueError("Invalid data format")
        
        classification = self.classifier.classify_error(error)
        
        assert classification.category == ErrorCategory.CONFIGURATION
        assert classification.severity == ErrorSeverity.MEDIUM
        assert classification.is_retryable is False
        assert classification.recovery_action == "log_and_skip"
    
    def test_should_notify_critical_error(self):
        """Test notification decision for critical errors."""
        classification = ErrorClassification(
            category=ErrorCategory.TRANSIENT,
            severity=ErrorSeverity.CRITICAL,
            is_retryable=True,
            should_circuit_break=True
        )
        
        assert self.classifier.should_notify(classification, 1) is True
    
    def test_should_notify_high_severity_error(self):
        """Test notification decision for high severity errors."""
        classification = ErrorClassification(
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.HIGH,
            is_retryable=False,
            should_circuit_break=False
        )
        
        assert self.classifier.should_notify(classification, 1) is True
    
    def test_should_notify_medium_severity_with_threshold(self):
        """Test notification decision for medium severity errors with threshold."""
        classification = ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False
        )
        
        # Should not notify for single occurrence
        assert self.classifier.should_notify(classification, 1) is False
        
        # Should notify when threshold is reached
        assert self.classifier.should_notify(classification, 3) is True
    
    def test_should_not_notify_low_severity(self):
        """Test notification decision for low severity errors."""
        classification = ErrorClassification(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.LOW,
            is_retryable=False,
            should_circuit_break=False
        )
        
        assert self.classifier.should_notify(classification, 10) is False
    
    def test_get_recovery_strategy(self):
        """Test recovery strategy retrieval."""
        classification = ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            recovery_action="exponential_backoff",
            max_retry_attempts=5,
            retry_delay_multiplier=2.0
        )
        
        strategy = self.classifier.get_recovery_strategy(classification)
        
        assert strategy['action'] == 'retry'
        assert strategy['max_attempts'] == 5
        assert strategy['delay_multiplier'] == 2.0
        assert 'exponential backoff' in strategy['description']


class TestCircuitBreaker:
    """Test circuit breaker functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        config = CircuitBreakerConfig(
            failure_threshold=3,
            success_threshold=2,
            timeout=1.0,  # Short timeout for testing
            reset_timeout=5.0
        )
        self.circuit_breaker = CircuitBreaker("test_circuit", config)
    
    def test_initial_state_is_closed(self):
        """Test that circuit breaker starts in CLOSED state."""
        assert self.circuit_breaker.get_state() == CircuitState.CLOSED
    
    def test_successful_call(self):
        """Test successful function call through circuit breaker."""
        def success_func():
            return "success"
        
        result = self.circuit_breaker.call(success_func)
        assert result == "success"
        
        stats = self.circuit_breaker.get_stats()
        assert stats.total_requests == 1
        assert stats.successful_requests == 1
        assert stats.failed_requests == 0
    
    def test_failed_call(self):
        """Test failed function call through circuit breaker."""
        def failing_func():
            raise ValueError("Test error")
        
        with pytest.raises(ValueError):
            self.circuit_breaker.call(failing_func)
        
        stats = self.circuit_breaker.get_stats()
        assert stats.total_requests == 1
        assert stats.successful_requests == 0
        assert stats.failed_requests == 1
    
    def test_circuit_opens_after_threshold_failures(self):
        """Test that circuit opens after reaching failure threshold."""
        def failing_func():
            raise ValueError("Test error")
        
        # Fail enough times to open the circuit
        for i in range(3):
            with pytest.raises(ValueError):
                self.circuit_breaker.call(failing_func)
        
        # Circuit should now be open
        assert self.circuit_breaker.get_state() == CircuitState.OPEN
        
        # Next call should be rejected
        with pytest.raises(CircuitBreakerError):
            self.circuit_breaker.call(failing_func)
        
        stats = self.circuit_breaker.get_stats()
        assert stats.rejected_requests == 1
    
    def test_circuit_transitions_to_half_open_after_timeout(self):
        """Test that circuit transitions to HALF_OPEN after timeout."""
        def failing_func():
            raise ValueError("Test error")
        
        # Open the circuit
        for i in range(3):
            with pytest.raises(ValueError):
                self.circuit_breaker.call(failing_func)
        
        assert self.circuit_breaker.get_state() == CircuitState.OPEN
        
        # Wait for timeout
        time.sleep(1.1)
        
        # Check state - should transition to HALF_OPEN
        assert self.circuit_breaker.get_state() == CircuitState.HALF_OPEN
    
    def test_circuit_closes_after_successful_calls_in_half_open(self):
        """Test that circuit closes after successful calls in HALF_OPEN state."""
        def failing_func():
            raise ValueError("Test error")
        
        def success_func():
            return "success"
        
        # Open the circuit
        for i in range(3):
            with pytest.raises(ValueError):
                self.circuit_breaker.call(failing_func)
        
        # Wait for timeout to go to HALF_OPEN
        time.sleep(1.1)
        assert self.circuit_breaker.get_state() == CircuitState.HALF_OPEN
        
        # Make successful calls to close the circuit
        for i in range(2):  # success_threshold = 2
            result = self.circuit_breaker.call(success_func)
            assert result == "success"
        
        # Circuit should now be closed
        assert self.circuit_breaker.get_state() == CircuitState.CLOSED
    
    def test_circuit_reopens_on_failure_in_half_open(self):
        """Test that circuit reopens on failure in HALF_OPEN state."""
        def failing_func():
            raise ValueError("Test error")
        
        # Open the circuit
        for i in range(3):
            with pytest.raises(ValueError):
                self.circuit_breaker.call(failing_func)
        
        # Wait for timeout to go to HALF_OPEN
        time.sleep(1.1)
        assert self.circuit_breaker.get_state() == CircuitState.HALF_OPEN
        
        # Fail in half-open state
        with pytest.raises(ValueError):
            self.circuit_breaker.call(failing_func)
        
        # Circuit should be open again
        assert self.circuit_breaker.get_state() == CircuitState.OPEN
    
    def test_reset_circuit_breaker(self):
        """Test resetting circuit breaker to initial state."""
        def failing_func():
            raise ValueError("Test error")
        
        # Open the circuit
        for i in range(3):
            with pytest.raises(ValueError):
                self.circuit_breaker.call(failing_func)
        
        assert self.circuit_breaker.get_state() == CircuitState.OPEN
        
        # Reset the circuit breaker
        self.circuit_breaker.reset()
        
        assert self.circuit_breaker.get_state() == CircuitState.CLOSED
        stats = self.circuit_breaker.get_stats()
        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
    
    def test_force_open_and_closed(self):
        """Test forcing circuit breaker states."""
        assert self.circuit_breaker.get_state() == CircuitState.CLOSED
        
        # Force open
        self.circuit_breaker.force_open()
        assert self.circuit_breaker.get_state() == CircuitState.OPEN
        
        # Force closed
        self.circuit_breaker.force_closed()
        assert self.circuit_breaker.get_state() == CircuitState.CLOSED


class TestCircuitBreakerManager:
    """Test circuit breaker manager functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        self.manager = CircuitBreakerManager()
    
    def test_get_or_create_breaker(self):
        """Test getting or creating circuit breakers."""
        config = CircuitBreakerConfig(failure_threshold=5)
        
        # Get new breaker
        breaker1 = self.manager.get_breaker("test1", config)
        assert breaker1.name == "test1"
        
        # Get same breaker again
        breaker2 = self.manager.get_breaker("test1")
        assert breaker1 is breaker2
    
    def test_call_with_breaker(self):
        """Test calling function with circuit breaker protection."""
        def success_func():
            return "success"
        
        result = self.manager.call_with_breaker("test_op", success_func)
        assert result == "success"
    
    def test_get_all_stats(self):
        """Test getting statistics for all circuit breakers."""
        def success_func():
            return "success"
        
        def failing_func():
            raise ValueError("Test error")
        
        # Create some activity
        self.manager.call_with_breaker("op1", success_func)
        
        try:
            self.manager.call_with_breaker("op2", failing_func)
        except ValueError:
            pass
        
        stats = self.manager.get_all_stats()
        
        assert "op1" in stats
        assert "op2" in stats
        assert stats["op1"]["successful_requests"] == 1
        assert stats["op2"]["failed_requests"] == 1
    
    def test_get_health_status(self):
        """Test getting overall health status."""
        def success_func():
            return "success"
        
        # Create some activity
        self.manager.call_with_breaker("healthy_op", success_func)
        
        health = self.manager.get_health_status()
        
        assert health["overall_health"] == "healthy"
        assert health["total_breakers"] == 1
        assert health["closed_breakers"] == 1
        assert health["open_breakers"] == 0
    
    def test_reset_all_breakers(self):
        """Test resetting all circuit breakers."""
        def success_func():
            return "success"
        
        # Create some activity
        self.manager.call_with_breaker("op1", success_func)
        self.manager.call_with_breaker("op2", success_func)
        
        # Reset all
        self.manager.reset_all()
        
        stats = self.manager.get_all_stats()
        for breaker_stats in stats.values():
            assert breaker_stats["total_requests"] == 0


class TestRecoveryManager:
    """Test recovery manager functionality."""
    
    def setup_method(self):
        """Set up test fixtures."""
        config = RecoveryConfig(
            max_retry_attempts=3,
            base_retry_delay=0.1,  # Short delay for testing
            max_retry_delay=1.0,
            circuit_breaker_enabled=True
        )
        self.recovery_manager = RecoveryManager(config=config)
    
    def test_successful_execution(self):
        """Test successful operation execution."""
        def success_func():
            return "success"
        
        result = self.recovery_manager.execute_with_recovery(
            "test_op", success_func
        )
        
        assert result.success is True
        assert result.result == "success"
        assert len(result.attempts) == 1
        assert result.attempts[0].success is True
        assert result.recovery_strategy == "direct_success"
    
    def test_retry_on_transient_error(self):
        """Test retry behavior on transient errors."""
        call_count = 0
        
        def flaky_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                # Simulate throttling error
                raise ClientError(
                    error_response={
                        'Error': {
                            'Code': 'ThrottlingException',
                            'Message': 'Request was throttled'
                        }
                    },
                    operation_name='TestOperation'
                )
            return "success"
        
        result = self.recovery_manager.execute_with_recovery(
            "test_op", flaky_func
        )
        
        assert result.success is True
        assert result.result == "success"
        assert len(result.attempts) == 3
        assert result.attempts[0].success is False
        assert result.attempts[1].success is False
        assert result.attempts[2].success is True
        assert result.recovery_strategy == "retry_success"
    
    def test_no_retry_on_non_retryable_error(self):
        """Test that non-retryable errors are not retried."""
        def failing_func():
            raise ClientError(
                error_response={
                    'Error': {
                        'Code': 'AccessDeniedException',
                        'Message': 'Access denied'
                    }
                },
                operation_name='TestOperation'
            )
        
        result = self.recovery_manager.execute_with_recovery(
            "test_op", failing_func
        )
        
        assert result.success is False
        assert len(result.attempts) == 1
        assert result.attempts[0].success is False
        assert isinstance(result.error, ClientError)
    
    def test_retry_exhaustion(self):
        """Test behavior when all retry attempts are exhausted."""
        def always_failing_func():
            raise ClientError(
                error_response={
                    'Error': {
                        'Code': 'ThrottlingException',
                        'Message': 'Request was throttled'
                    }
                },
                operation_name='TestOperation'
            )
        
        result = self.recovery_manager.execute_with_recovery(
            "test_op", always_failing_func
        )
        
        assert result.success is False
        assert len(result.attempts) == 3  # max_retry_attempts
        assert all(not attempt.success for attempt in result.attempts)
        assert result.recovery_strategy == "retry_exhausted"
    
    def test_circuit_breaker_integration(self):
        """Test integration with circuit breaker."""
        def failing_func():
            raise ClientError(
                error_response={
                    'Error': {
                        'Code': 'ServiceUnavailableException',
                        'Message': 'Service unavailable'
                    }
                },
                operation_name='TestOperation'
            )
        
        # Make enough failures to open circuit breaker
        for i in range(5):
            result = self.recovery_manager.execute_with_recovery(
                "circuit_test_op", failing_func
            )
            assert result.success is False
        
        # Next call should be blocked by circuit breaker
        result = self.recovery_manager.execute_with_recovery(
            "circuit_test_op", failing_func
        )
        
        assert result.success is False
        assert isinstance(result.error, CircuitBreakerError)
        assert result.recovery_strategy == "circuit_breaker_blocked"
    
    def test_execute_with_fallback_success_primary(self):
        """Test fallback execution when primary succeeds."""
        def primary_func():
            return "primary_success"
        
        def fallback_func():
            return "fallback_success"
        
        result = self.recovery_manager.execute_with_fallback(
            primary_func, fallback_func, "test_op"
        )
        
        assert result.success is True
        assert result.result == "primary_success"
        assert result.recovery_strategy == "primary_success"
    
    def test_execute_with_fallback_success_fallback(self):
        """Test fallback execution when primary fails but fallback succeeds."""
        def primary_func():
            raise ValueError("Primary failed")
        
        def fallback_func():
            return "fallback_success"
        
        result = self.recovery_manager.execute_with_fallback(
            primary_func, fallback_func, "test_op"
        )
        
        assert result.success is True
        assert result.result == "fallback_success"
        assert result.recovery_strategy == "fallback_success"
    
    def test_execute_with_fallback_both_fail(self):
        """Test fallback execution when both primary and fallback fail."""
        def primary_func():
            raise ValueError("Primary failed")
        
        def fallback_func():
            raise ValueError("Fallback failed")
        
        result = self.recovery_manager.execute_with_fallback(
            primary_func, fallback_func, "test_op"
        )
        
        assert result.success is False
        assert result.recovery_strategy == "both_failed"
    
    def test_get_recovery_recommendations(self):
        """Test getting recovery recommendations for errors."""
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'AccessDeniedException',
                    'Message': 'Access denied'
                }
            },
            operation_name='TestOperation'
        )
        
        recommendations = self.recovery_manager.get_recovery_recommendations(error)
        
        assert recommendations['error_category'] == 'permission'
        assert recommendations['error_severity'] == 'high'
        assert recommendations['is_retryable'] is False
        assert 'IAM permissions' in str(recommendations['recommended_actions'])
    
    def test_get_health_status(self):
        """Test getting health status of recovery manager."""
        health = self.recovery_manager.get_health_status()
        
        assert 'recovery_manager' in health
        assert 'circuit_breakers' in health
        assert health['recovery_manager']['status'] == 'healthy'
        assert 'config' in health['recovery_manager']


class TestErrorHandlingIntegration:
    """Integration tests for error handling components."""
    
    def test_end_to_end_error_handling(self):
        """Test complete error handling flow."""
        config = RecoveryConfig(
            max_retry_attempts=2,
            base_retry_delay=0.1,
            circuit_breaker_enabled=True,
            circuit_breaker_failure_threshold=3
        )
        recovery_manager = RecoveryManager(config=config)
        
        call_count = 0
        
        def flaky_service():
            nonlocal call_count
            call_count += 1
            
            if call_count == 1:
                # First call: throttling error (retryable)
                raise ClientError(
                    error_response={
                        'Error': {
                            'Code': 'ThrottlingException',
                            'Message': 'Request was throttled'
                        }
                    },
                    operation_name='TestOperation'
                )
            elif call_count == 2:
                # Second call: success
                return "success_after_retry"
            else:
                # Should not reach here in this test
                raise Exception("Unexpected call")
        
        result = recovery_manager.execute_with_recovery(
            "integration_test", flaky_service
        )
        
        # Verify successful recovery
        assert result.success is True
        assert result.result == "success_after_retry"
        assert len(result.attempts) == 2
        assert result.attempts[0].success is False
        assert result.attempts[1].success is True
        
        # Verify circuit breaker is still closed
        cb_manager = recovery_manager.circuit_breaker_manager
        health = cb_manager.get_health_status()
        assert health['overall_health'] == 'healthy'
    
    def test_error_classification_with_context(self):
        """Test error classification with context information."""
        classifier = ErrorClassifier()
        
        error = ClientError(
            error_response={
                'Error': {
                    'Code': 'UnknownErrorCode',
                    'Message': 'Unknown error'
                }
            },
            operation_name='TestOperation'
        )
        
        context = {
            'account_id': '123456789012',
            'operation': 'put_contact_information',
            'retry_count': 1
        }
        
        classification = classifier.classify_error(error, context)
        
        # Should classify as unknown but retryable
        assert classification.category == ErrorCategory.UNKNOWN
        assert classification.is_retryable is True
        assert classification.max_retry_attempts == 2
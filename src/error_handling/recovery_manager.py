"""
Error recovery manager for AWS Contact Sync system.

Provides comprehensive error recovery mechanisms including retry logic,
circuit breaker integration, and recovery strategies based on error classification.
"""

import time
import random
import logging
from typing import Dict, Any, Optional, Callable, Union, List
from dataclasses import dataclass
from datetime import datetime, timedelta

from .error_classifier import ErrorClassifier, ErrorClassification, ErrorCategory, ErrorSeverity
from .circuit_breaker import CircuitBreakerManager, CircuitBreakerConfig, CircuitBreakerError

logger = logging.getLogger(__name__)


@dataclass
class RecoveryConfig:
    """Configuration for error recovery behavior."""
    max_retry_attempts: int = 3
    base_retry_delay: float = 2.0
    max_retry_delay: float = 60.0
    jitter_factor: float = 0.1
    circuit_breaker_enabled: bool = True
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_timeout: float = 60.0
    enable_exponential_backoff: bool = True
    enable_jitter: bool = True


@dataclass
class RecoveryAttempt:
    """Information about a recovery attempt."""
    attempt_number: int
    timestamp: datetime
    error: Optional[Exception] = None
    success: bool = False
    delay_before_attempt: float = 0.0
    recovery_action: Optional[str] = None


@dataclass
class RecoveryResult:
    """Result of a recovery operation."""
    success: bool
    result: Any = None
    error: Optional[Exception] = None
    attempts: List[RecoveryAttempt] = None
    total_duration: float = 0.0
    recovery_strategy: Optional[str] = None


class RecoveryManager:
    """
    Manages error recovery strategies and execution.
    
    Integrates error classification, retry logic, circuit breakers, and
    recovery strategies to provide comprehensive error handling.
    """
    
    def __init__(self, 
                 config: Optional[RecoveryConfig] = None,
                 error_classifier: Optional[ErrorClassifier] = None,
                 circuit_breaker_manager: Optional[CircuitBreakerManager] = None):
        """
        Initialize recovery manager.
        
        Args:
            config: Recovery configuration
            error_classifier: Error classifier instance
            circuit_breaker_manager: Circuit breaker manager instance
        """
        self.config = config or RecoveryConfig()
        self.error_classifier = error_classifier or ErrorClassifier()
        self.circuit_breaker_manager = circuit_breaker_manager or CircuitBreakerManager()
        
        logger.info(f"Initialized recovery manager with config: {self.config}")
    
    def execute_with_recovery(self, 
                            operation_name: str,
                            operation_func: Callable,
                            context: Optional[Dict[str, Any]] = None,
                            *args, **kwargs) -> RecoveryResult:
        """
        Execute an operation with comprehensive error recovery.
        
        Args:
            operation_name: Name of the operation for logging and circuit breaker
            operation_func: Function to execute
            context: Optional context information
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            RecoveryResult containing execution results
        """
        start_time = time.time()
        attempts = []
        last_error = None
        
        logger.info(f"Starting recovery-enabled execution of '{operation_name}'")
        
        # Configure circuit breaker for this operation
        if self.config.circuit_breaker_enabled:
            cb_config = CircuitBreakerConfig(
                failure_threshold=self.config.circuit_breaker_failure_threshold,
                timeout=self.config.circuit_breaker_timeout
            )
            circuit_breaker = self.circuit_breaker_manager.get_breaker(operation_name, cb_config)
        
        for attempt_num in range(1, self.config.max_retry_attempts + 1):
            attempt_start = time.time()
            
            try:
                # Execute through circuit breaker if enabled
                if self.config.circuit_breaker_enabled:
                    result = circuit_breaker.call(operation_func, *args, **kwargs)
                else:
                    result = operation_func(*args, **kwargs)
                
                # Success!
                attempt = RecoveryAttempt(
                    attempt_number=attempt_num,
                    timestamp=datetime.utcnow(),
                    success=True,
                    delay_before_attempt=0.0 if attempt_num == 1 else attempts[-1].delay_before_attempt
                )
                attempts.append(attempt)
                
                total_duration = time.time() - start_time
                
                if attempt_num > 1:
                    logger.info(f"Operation '{operation_name}' succeeded after {attempt_num} attempts "
                               f"in {total_duration:.2f}s")
                
                return RecoveryResult(
                    success=True,
                    result=result,
                    attempts=attempts,
                    total_duration=total_duration,
                    recovery_strategy="retry_success" if attempt_num > 1 else "direct_success"
                )
                
            except CircuitBreakerError as e:
                # Circuit breaker is open, don't retry
                logger.error(f"Circuit breaker is open for '{operation_name}': {e}")
                
                attempt = RecoveryAttempt(
                    attempt_number=attempt_num,
                    timestamp=datetime.utcnow(),
                    error=e,
                    success=False,
                    recovery_action="circuit_breaker_open"
                )
                attempts.append(attempt)
                
                return RecoveryResult(
                    success=False,
                    error=e,
                    attempts=attempts,
                    total_duration=time.time() - start_time,
                    recovery_strategy="circuit_breaker_blocked"
                )
                
            except Exception as e:
                last_error = e
                
                # Classify the error to determine recovery strategy
                classification = self.error_classifier.classify_error(e, context)
                
                logger.warning(f"Operation '{operation_name}' failed on attempt {attempt_num}: "
                              f"{type(e).__name__}: {e}")
                logger.debug(f"Error classification: {classification.category.value}, "
                            f"retryable: {classification.is_retryable}")
                
                # Record the attempt
                attempt = RecoveryAttempt(
                    attempt_number=attempt_num,
                    timestamp=datetime.utcnow(),
                    error=e,
                    success=False,
                    recovery_action=classification.recovery_action
                )
                attempts.append(attempt)
                
                # Check if we should retry
                if not classification.is_retryable:
                    logger.error(f"Non-retryable error for '{operation_name}': {classification.user_message}")
                    break
                
                # Check if this is the last attempt
                if attempt_num >= self.config.max_retry_attempts:
                    logger.error(f"All retry attempts exhausted for '{operation_name}'")
                    break
                
                # Calculate delay before next attempt
                delay = self._calculate_retry_delay(
                    attempt_num, 
                    classification,
                    self.config.base_retry_delay,
                    self.config.max_retry_delay
                )
                
                attempt.delay_before_attempt = delay
                
                logger.info(f"Retrying '{operation_name}' in {delay:.2f}s "
                           f"(attempt {attempt_num + 1}/{self.config.max_retry_attempts})")
                
                time.sleep(delay)
        
        # All attempts failed
        total_duration = time.time() - start_time
        
        logger.error(f"Operation '{operation_name}' failed after {len(attempts)} attempts "
                    f"in {total_duration:.2f}s")
        
        return RecoveryResult(
            success=False,
            error=last_error,
            attempts=attempts,
            total_duration=total_duration,
            recovery_strategy="retry_exhausted"
        )
    
    def _calculate_retry_delay(self, 
                              attempt_num: int, 
                              classification: ErrorClassification,
                              base_delay: float,
                              max_delay: float) -> float:
        """
        Calculate delay before next retry attempt.
        
        Args:
            attempt_num: Current attempt number (1-based)
            classification: Error classification
            base_delay: Base delay in seconds
            max_delay: Maximum delay in seconds
            
        Returns:
            Delay in seconds
        """
        # Use classification-specific delay multiplier if available
        multiplier = classification.retry_delay_multiplier
        
        if self.config.enable_exponential_backoff:
            # Exponential backoff: base_delay * multiplier^(attempt_num - 1)
            delay = base_delay * (multiplier ** (attempt_num - 1))
        else:
            # Linear backoff: base_delay * multiplier * attempt_num
            delay = base_delay * multiplier * attempt_num
        
        # Cap at maximum delay
        delay = min(delay, max_delay)
        
        # Add jitter to prevent thundering herd
        if self.config.enable_jitter:
            jitter_range = delay * self.config.jitter_factor
            jitter = random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, delay + jitter)  # Ensure minimum delay
        
        return delay
    
    def execute_with_fallback(self,
                            primary_operation: Callable,
                            fallback_operation: Callable,
                            operation_name: str,
                            context: Optional[Dict[str, Any]] = None,
                            *args, **kwargs) -> RecoveryResult:
        """
        Execute operation with fallback if primary fails.
        
        Args:
            primary_operation: Primary operation to try first
            fallback_operation: Fallback operation if primary fails
            operation_name: Name for logging
            context: Optional context
            *args: Arguments for operations
            **kwargs: Keyword arguments for operations
            
        Returns:
            RecoveryResult from primary or fallback operation
        """
        logger.info(f"Executing '{operation_name}' with fallback")
        
        # Try primary operation first
        primary_result = self.execute_with_recovery(
            f"{operation_name}_primary",
            primary_operation,
            context,
            *args, **kwargs
        )
        
        if primary_result.success:
            primary_result.recovery_strategy = "primary_success"
            return primary_result
        
        # Primary failed, try fallback
        logger.warning(f"Primary operation failed for '{operation_name}', trying fallback")
        
        fallback_result = self.execute_with_recovery(
            f"{operation_name}_fallback",
            fallback_operation,
            context,
            *args, **kwargs
        )
        
        if fallback_result.success:
            fallback_result.recovery_strategy = "fallback_success"
            logger.info(f"Fallback operation succeeded for '{operation_name}'")
        else:
            fallback_result.recovery_strategy = "both_failed"
            logger.error(f"Both primary and fallback operations failed for '{operation_name}'")
        
        return fallback_result
    
    def get_recovery_recommendations(self, error: Exception, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Get recovery recommendations for a specific error.
        
        Args:
            error: The exception that occurred
            context: Optional context information
            
        Returns:
            Dict containing recovery recommendations
        """
        classification = self.error_classifier.classify_error(error, context)
        recovery_strategy = self.error_classifier.get_recovery_strategy(classification)
        
        recommendations = {
            'error_category': classification.category.value,
            'error_severity': classification.severity.value,
            'is_retryable': classification.is_retryable,
            'should_circuit_break': classification.should_circuit_break,
            'recovery_strategy': recovery_strategy,
            'user_message': classification.user_message,
            'recommended_actions': []
        }
        
        # Add specific recommendations based on error category
        if classification.category == ErrorCategory.PERMISSION:
            recommendations['recommended_actions'].extend([
                'Check IAM permissions for the service account',
                'Verify account is not in the exclusion list',
                'Ensure cross-account trust relationships are configured'
            ])
        
        elif classification.category == ErrorCategory.RATE_LIMIT:
            recommendations['recommended_actions'].extend([
                'Implement exponential backoff with jitter',
                'Consider reducing request rate',
                'Check if API quotas need to be increased'
            ])
        
        elif classification.category == ErrorCategory.NETWORK:
            recommendations['recommended_actions'].extend([
                'Check network connectivity',
                'Verify DNS resolution',
                'Consider increasing timeout values'
            ])
        
        elif classification.category == ErrorCategory.CONFIGURATION:
            recommendations['recommended_actions'].extend([
                'Validate configuration parameters',
                'Check resource existence and accessibility',
                'Review input data format and values'
            ])
        
        elif classification.category == ErrorCategory.TRANSIENT:
            recommendations['recommended_actions'].extend([
                'Retry with exponential backoff',
                'Monitor service health status',
                'Consider circuit breaker activation'
            ])
        
        return recommendations
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get health status of the recovery manager.
        
        Returns:
            Dict containing health information
        """
        cb_health = self.circuit_breaker_manager.get_health_status()
        
        return {
            'recovery_manager': {
                'status': 'healthy',
                'config': {
                    'max_retry_attempts': self.config.max_retry_attempts,
                    'circuit_breaker_enabled': self.config.circuit_breaker_enabled,
                    'exponential_backoff_enabled': self.config.enable_exponential_backoff
                }
            },
            'circuit_breakers': cb_health
        }
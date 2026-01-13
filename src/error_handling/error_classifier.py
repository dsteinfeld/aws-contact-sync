"""
Error classification and handling strategies for AWS Contact Sync system.

Provides comprehensive error classification, handling strategies, and recovery mechanisms
for different types of errors that can occur during contact synchronization operations.
"""

import logging
from enum import Enum
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger(__name__)


class ErrorCategory(Enum):
    """Categories of errors for classification and handling."""
    TRANSIENT = "transient"          # Temporary errors that should be retried
    PERMANENT = "permanent"          # Permanent errors that should not be retried
    PERMISSION = "permission"        # Permission/authorization errors
    CONFIGURATION = "configuration"  # Configuration or validation errors
    RATE_LIMIT = "rate_limit"       # Rate limiting errors
    NETWORK = "network"             # Network connectivity errors
    UNKNOWN = "unknown"             # Unknown or unclassified errors


class ErrorSeverity(Enum):
    """Severity levels for error handling and alerting."""
    LOW = "low"          # Minor issues, log only
    MEDIUM = "medium"    # Moderate issues, may require attention
    HIGH = "high"        # Serious issues, require immediate attention
    CRITICAL = "critical" # Critical system failures


@dataclass
class ErrorClassification:
    """Classification result for an error."""
    category: ErrorCategory
    severity: ErrorSeverity
    is_retryable: bool
    should_circuit_break: bool
    retry_delay_multiplier: float = 1.0
    max_retry_attempts: Optional[int] = None
    recovery_action: Optional[str] = None
    user_message: Optional[str] = None


class ErrorClassifier:
    """Classifies errors and determines appropriate handling strategies."""
    
    # AWS API error code mappings
    AWS_ERROR_MAPPINGS = {
        # Transient errors - should be retried
        'ThrottlingException': ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=2.0,
            max_retry_attempts=5,
            recovery_action="exponential_backoff",
            user_message="Request was throttled, retrying with backoff"
        ),
        'TooManyRequestsException': ErrorClassification(
            category=ErrorCategory.RATE_LIMIT,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=2.0,
            max_retry_attempts=5,
            recovery_action="exponential_backoff",
            user_message="Too many requests, retrying with backoff"
        ),
        'ServiceUnavailableException': ErrorClassification(
            category=ErrorCategory.TRANSIENT,
            severity=ErrorSeverity.HIGH,
            is_retryable=True,
            should_circuit_break=True,
            retry_delay_multiplier=1.5,
            max_retry_attempts=3,
            recovery_action="circuit_breaker",
            user_message="AWS service temporarily unavailable"
        ),
        'InternalServerError': ErrorClassification(
            category=ErrorCategory.TRANSIENT,
            severity=ErrorSeverity.HIGH,
            is_retryable=True,
            should_circuit_break=True,
            retry_delay_multiplier=1.5,
            max_retry_attempts=3,
            recovery_action="circuit_breaker",
            user_message="AWS internal server error"
        ),
        'RequestTimeout': ErrorClassification(
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=1.0,
            max_retry_attempts=3,
            recovery_action="retry",
            user_message="Request timed out"
        ),
        'RequestTimeoutException': ErrorClassification(
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=1.0,
            max_retry_attempts=3,
            recovery_action="retry",
            user_message="Request timed out"
        ),
        
        # Permission errors - not retryable
        'AccessDeniedException': ErrorClassification(
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.HIGH,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="skip_account",
            user_message="Insufficient permissions to access account"
        ),
        'UnauthorizedOperation': ErrorClassification(
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.HIGH,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="skip_account",
            user_message="Operation not authorized"
        ),
        'ForbiddenException': ErrorClassification(
            category=ErrorCategory.PERMISSION,
            severity=ErrorSeverity.HIGH,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="skip_account",
            user_message="Access forbidden"
        ),
        
        # Configuration/validation errors - not retryable
        'ValidationException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="log_and_skip",
            user_message="Invalid request parameters"
        ),
        'InvalidParameterValue': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="log_and_skip",
            user_message="Invalid parameter value"
        ),
        'ResourceNotFoundException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="skip_account",
            user_message="Resource not found"
        ),
        'AccountNotFoundException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="skip_account",
            user_message="Account not found"
        ),
        'ConflictException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="log_and_skip",
            user_message="Resource conflict"
        ),
        
        # Organization-specific errors
        'AWSOrganizationsNotInUseException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.CRITICAL,
            is_retryable=False,
            should_circuit_break=True,
            recovery_action="abort_operation",
            user_message="AWS Organizations is not enabled"
        ),
        'OrganizationNotFoundException': ErrorClassification(
            category=ErrorCategory.CONFIGURATION,
            severity=ErrorSeverity.CRITICAL,
            is_retryable=False,
            should_circuit_break=True,
            recovery_action="abort_operation",
            user_message="Organization not found"
        ),
    }
    
    def classify_error(self, error: Exception, context: Optional[Dict[str, Any]] = None) -> ErrorClassification:
        """
        Classify an error and determine handling strategy.
        
        Args:
            error: The exception that occurred
            context: Optional context information about the operation
            
        Returns:
            ErrorClassification with handling strategy
        """
        try:
            # Handle AWS ClientError
            if isinstance(error, ClientError):
                return self._classify_client_error(error, context)
            
            # Handle BotoCoreError (network/connection issues)
            elif isinstance(error, BotoCoreError):
                return self._classify_botocore_error(error, context)
            
            # Handle Python built-in exceptions
            elif isinstance(error, (ConnectionError, TimeoutError)):
                return ErrorClassification(
                    category=ErrorCategory.NETWORK,
                    severity=ErrorSeverity.MEDIUM,
                    is_retryable=True,
                    should_circuit_break=False,
                    retry_delay_multiplier=1.0,
                    max_retry_attempts=3,
                    recovery_action="retry",
                    user_message="Network connectivity issue"
                )
            
            elif isinstance(error, ValueError):
                return ErrorClassification(
                    category=ErrorCategory.CONFIGURATION,
                    severity=ErrorSeverity.MEDIUM,
                    is_retryable=False,
                    should_circuit_break=False,
                    recovery_action="log_and_skip",
                    user_message="Invalid data format"
                )
            
            # Unknown error
            else:
                return self._classify_unknown_error(error, context)
                
        except Exception as e:
            logger.error(f"Error during error classification: {e}")
            return self._get_default_classification()
    
    def _classify_client_error(self, error: ClientError, context: Optional[Dict[str, Any]]) -> ErrorClassification:
        """Classify AWS ClientError."""
        error_code = error.response.get('Error', {}).get('Code', 'Unknown')
        error_message = error.response.get('Error', {}).get('Message', str(error))
        
        # Check for known error codes
        if error_code in self.AWS_ERROR_MAPPINGS:
            classification = self.AWS_ERROR_MAPPINGS[error_code]
            logger.info(f"Classified AWS error {error_code} as {classification.category.value}")
            return classification
        
        # Handle unknown AWS error codes
        logger.warning(f"Unknown AWS error code: {error_code} - {error_message}")
        
        # Try to infer category from error code patterns
        if 'throttl' in error_code.lower() or 'limit' in error_code.lower():
            return ErrorClassification(
                category=ErrorCategory.RATE_LIMIT,
                severity=ErrorSeverity.MEDIUM,
                is_retryable=True,
                should_circuit_break=False,
                retry_delay_multiplier=2.0,
                max_retry_attempts=5,
                recovery_action="exponential_backoff",
                user_message=f"Rate limiting error: {error_code}"
            )
        
        elif 'access' in error_code.lower() or 'denied' in error_code.lower() or 'unauthorized' in error_code.lower():
            return ErrorClassification(
                category=ErrorCategory.PERMISSION,
                severity=ErrorSeverity.HIGH,
                is_retryable=False,
                should_circuit_break=False,
                recovery_action="skip_account",
                user_message=f"Permission error: {error_code}"
            )
        
        elif 'invalid' in error_code.lower() or 'validation' in error_code.lower():
            return ErrorClassification(
                category=ErrorCategory.CONFIGURATION,
                severity=ErrorSeverity.MEDIUM,
                is_retryable=False,
                should_circuit_break=False,
                recovery_action="log_and_skip",
                user_message=f"Configuration error: {error_code}"
            )
        
        # Default for unknown AWS errors
        return ErrorClassification(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=1.0,
            max_retry_attempts=2,
            recovery_action="retry",
            user_message=f"Unknown AWS error: {error_code}"
        )
    
    def _classify_botocore_error(self, error: BotoCoreError, context: Optional[Dict[str, Any]]) -> ErrorClassification:
        """Classify BotoCoreError (network/connection issues)."""
        error_type = type(error).__name__
        
        logger.info(f"Classifying BotoCoreError: {error_type}")
        
        # Most BotoCoreErrors are network-related and retryable
        return ErrorClassification(
            category=ErrorCategory.NETWORK,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=True,
            should_circuit_break=False,
            retry_delay_multiplier=1.0,
            max_retry_attempts=3,
            recovery_action="retry",
            user_message=f"Network error: {error_type}"
        )
    
    def _classify_unknown_error(self, error: Exception, context: Optional[Dict[str, Any]]) -> ErrorClassification:
        """Classify unknown errors."""
        error_type = type(error).__name__
        error_message = str(error)
        
        logger.warning(f"Classifying unknown error: {error_type} - {error_message}")
        
        # Conservative approach for unknown errors
        return ErrorClassification(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,  # Don't retry unknown errors by default
            should_circuit_break=False,
            recovery_action="log_and_skip",
            user_message=f"Unknown error: {error_type}"
        )
    
    def _get_default_classification(self) -> ErrorClassification:
        """Get default error classification for fallback."""
        return ErrorClassification(
            category=ErrorCategory.UNKNOWN,
            severity=ErrorSeverity.MEDIUM,
            is_retryable=False,
            should_circuit_break=False,
            recovery_action="log_and_skip",
            user_message="Unclassified error occurred"
        )
    
    def should_notify(self, classification: ErrorClassification, error_count: int = 1) -> bool:
        """
        Determine if an error should trigger notifications.
        
        Args:
            classification: Error classification
            error_count: Number of similar errors (for threshold-based notifications)
            
        Returns:
            bool: True if notification should be sent
        """
        # Always notify for critical errors
        if classification.severity == ErrorSeverity.CRITICAL:
            return True
        
        # Notify for high severity errors
        if classification.severity == ErrorSeverity.HIGH:
            return True
        
        # Notify for medium severity errors if they occur frequently
        if classification.severity == ErrorSeverity.MEDIUM and error_count >= 3:
            return True
        
        # Don't notify for low severity errors
        return False
    
    def get_recovery_strategy(self, classification: ErrorClassification) -> Dict[str, Any]:
        """
        Get recovery strategy based on error classification.
        
        Args:
            classification: Error classification
            
        Returns:
            Dict containing recovery strategy details
        """
        strategies = {
            "retry": {
                "action": "retry",
                "max_attempts": classification.max_retry_attempts or 3,
                "delay_multiplier": classification.retry_delay_multiplier,
                "description": "Retry the operation with standard backoff"
            },
            "exponential_backoff": {
                "action": "retry",
                "max_attempts": classification.max_retry_attempts or 5,
                "delay_multiplier": classification.retry_delay_multiplier,
                "description": "Retry with exponential backoff for rate limiting"
            },
            "circuit_breaker": {
                "action": "circuit_break",
                "cooldown_period": 300,  # 5 minutes
                "description": "Activate circuit breaker to prevent cascading failures"
            },
            "skip_account": {
                "action": "skip",
                "description": "Skip this account and continue with others"
            },
            "log_and_skip": {
                "action": "skip",
                "description": "Log the error and skip this operation"
            },
            "abort_operation": {
                "action": "abort",
                "description": "Abort the entire synchronization operation"
            }
        }
        
        return strategies.get(classification.recovery_action, strategies["log_and_skip"])
"""
Circuit breaker pattern implementation for AWS Contact Sync system.

Provides circuit breaker functionality to prevent cascading failures and
allow systems to recover from transient issues by temporarily stopping
requests to failing services.
"""

import time
import logging
from enum import Enum
from typing import Dict, Any, Optional, Callable, Union
from dataclasses import dataclass, field
from threading import Lock
import json

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests allowed
    OPEN = "open"          # Circuit is open, requests blocked
    HALF_OPEN = "half_open"  # Testing if service has recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker behavior."""
    failure_threshold: int = 5          # Number of failures before opening
    success_threshold: int = 3          # Number of successes to close from half-open
    timeout: float = 60.0              # Seconds to wait before trying half-open
    reset_timeout: float = 300.0       # Seconds to reset failure count
    max_timeout: float = 3600.0        # Maximum timeout (1 hour)
    backoff_multiplier: float = 2.0    # Multiplier for timeout on repeated failures


@dataclass
class CircuitBreakerStats:
    """Statistics for circuit breaker monitoring."""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    rejected_requests: int = 0
    state_changes: int = 0
    last_failure_time: Optional[float] = None
    last_success_time: Optional[float] = None
    current_timeout: float = field(default_factory=lambda: 60.0)


class CircuitBreakerError(Exception):
    """Exception raised when circuit breaker is open."""
    
    def __init__(self, message: str, circuit_name: str, retry_after: float):
        super().__init__(message)
        self.circuit_name = circuit_name
        self.retry_after = retry_after


class CircuitBreaker:
    """
    Circuit breaker implementation with configurable thresholds and timeouts.
    
    The circuit breaker monitors the success/failure rate of operations and
    can temporarily block requests to allow failing services to recover.
    """
    
    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        """
        Initialize circuit breaker.
        
        Args:
            name: Unique name for this circuit breaker
            config: Configuration options
        """
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.stats = CircuitBreakerStats()
        self.stats.current_timeout = self.config.timeout
        self.state = CircuitState.CLOSED
        self._lock = Lock()
        
        # Track consecutive failures and successes
        self._consecutive_failures = 0
        self._consecutive_successes = 0
        self._last_state_change = time.time()
        
        logger.info(f"Initialized circuit breaker '{name}' with config: {self.config}")
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function through the circuit breaker.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
            
        Raises:
            CircuitBreakerError: If circuit is open
            Exception: Any exception raised by the function
        """
        with self._lock:
            self.stats.total_requests += 1
            
            # Check if circuit should be opened due to timeout
            self._check_timeout_state()
            
            # If circuit is open, reject the request
            if self.state == CircuitState.OPEN:
                self.stats.rejected_requests += 1
                retry_after = self._get_retry_after_time()
                
                logger.warning(f"Circuit breaker '{self.name}' is OPEN, rejecting request. Retry after {retry_after}s")
                raise CircuitBreakerError(
                    f"Circuit breaker '{self.name}' is open",
                    self.name,
                    retry_after
                )
        
        # Execute the function
        try:
            result = func(*args, **kwargs)
            self._record_success()
            return result
            
        except Exception as e:
            self._record_failure(e)
            raise
    
    def _record_success(self) -> None:
        """Record a successful operation."""
        with self._lock:
            self.stats.successful_requests += 1
            self.stats.last_success_time = time.time()
            self._consecutive_failures = 0
            self._consecutive_successes += 1
            
            logger.debug(f"Circuit breaker '{self.name}' recorded success. "
                        f"Consecutive successes: {self._consecutive_successes}")
            
            # If we're in half-open state and have enough successes, close the circuit
            if (self.state == CircuitState.HALF_OPEN and 
                self._consecutive_successes >= self.config.success_threshold):
                self._transition_to_closed()
    
    def _record_failure(self, error: Exception) -> None:
        """Record a failed operation."""
        with self._lock:
            self.stats.failed_requests += 1
            self.stats.last_failure_time = time.time()
            self._consecutive_successes = 0
            self._consecutive_failures += 1
            
            logger.warning(f"Circuit breaker '{self.name}' recorded failure: {error}. "
                          f"Consecutive failures: {self._consecutive_failures}")
            
            # If we have too many consecutive failures, open the circuit
            if self._consecutive_failures >= self.config.failure_threshold:
                self._transition_to_open()
    
    def _transition_to_open(self) -> None:
        """Transition circuit breaker to OPEN state."""
        if self.state != CircuitState.OPEN:
            old_state = self.state
            self.state = CircuitState.OPEN
            self.stats.state_changes += 1
            self._last_state_change = time.time()
            
            # Set current timeout to the configured timeout (not increased on first failure)
            self.stats.current_timeout = self.config.timeout
            
            logger.error(f"Circuit breaker '{self.name}' transitioned from {old_state.value} to OPEN. "
                        f"Timeout: {self.stats.current_timeout}s")
    
    def _transition_to_half_open(self) -> None:
        """Transition circuit breaker to HALF_OPEN state."""
        if self.state != CircuitState.HALF_OPEN:
            old_state = self.state
            self.state = CircuitState.HALF_OPEN
            self.stats.state_changes += 1
            self._last_state_change = time.time()
            self._consecutive_successes = 0
            
            logger.info(f"Circuit breaker '{self.name}' transitioned from {old_state.value} to HALF_OPEN")
    
    def _transition_to_closed(self) -> None:
        """Transition circuit breaker to CLOSED state."""
        if self.state != CircuitState.CLOSED:
            old_state = self.state
            self.state = CircuitState.CLOSED
            self.stats.state_changes += 1
            self._last_state_change = time.time()
            self._consecutive_failures = 0
            
            # Reset timeout on successful recovery
            self.stats.current_timeout = self.config.timeout
            
            logger.info(f"Circuit breaker '{self.name}' transitioned from {old_state.value} to CLOSED")
    
    def _check_timeout_state(self) -> None:
        """Check if circuit should transition from OPEN to HALF_OPEN based on timeout."""
        if self.state == CircuitState.OPEN:
            time_since_open = time.time() - self._last_state_change
            if time_since_open >= self.stats.current_timeout:
                self._transition_to_half_open()
    
    def _get_retry_after_time(self) -> float:
        """Get the time (in seconds) after which requests can be retried."""
        if self.state == CircuitState.OPEN:
            time_since_open = time.time() - self._last_state_change
            return max(0, self.stats.current_timeout - time_since_open)
        return 0
    
    def get_state(self) -> CircuitState:
        """Get current circuit breaker state."""
        with self._lock:
            self._check_timeout_state()
            return self.state
    
    def get_stats(self) -> CircuitBreakerStats:
        """Get circuit breaker statistics."""
        with self._lock:
            return CircuitBreakerStats(
                total_requests=self.stats.total_requests,
                successful_requests=self.stats.successful_requests,
                failed_requests=self.stats.failed_requests,
                rejected_requests=self.stats.rejected_requests,
                state_changes=self.stats.state_changes,
                last_failure_time=self.stats.last_failure_time,
                last_success_time=self.stats.last_success_time,
                current_timeout=self.stats.current_timeout
            )
    
    def reset(self) -> None:
        """Reset circuit breaker to initial state."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.stats = CircuitBreakerStats()
            self.stats.current_timeout = self.config.timeout
            self._consecutive_failures = 0
            self._consecutive_successes = 0
            self._last_state_change = time.time()
            
            logger.info(f"Circuit breaker '{self.name}' has been reset")
    
    def force_open(self) -> None:
        """Force circuit breaker to OPEN state (for testing/maintenance)."""
        with self._lock:
            self._transition_to_open()
            logger.warning(f"Circuit breaker '{self.name}' was forced to OPEN state")
    
    def force_closed(self) -> None:
        """Force circuit breaker to CLOSED state (for testing/recovery)."""
        with self._lock:
            self._transition_to_closed()
            logger.info(f"Circuit breaker '{self.name}' was forced to CLOSED state")


class CircuitBreakerManager:
    """
    Manages multiple circuit breakers for different services/operations.
    
    Provides a centralized way to create, configure, and monitor circuit breakers
    across the application.
    """
    
    def __init__(self):
        """Initialize circuit breaker manager."""
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = Lock()
        
        logger.info("Initialized circuit breaker manager")
    
    def get_breaker(self, name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
        """
        Get or create a circuit breaker.
        
        Args:
            name: Circuit breaker name
            config: Configuration (only used when creating new breaker)
            
        Returns:
            CircuitBreaker instance
        """
        with self._lock:
            if name not in self._breakers:
                self._breakers[name] = CircuitBreaker(name, config)
                logger.info(f"Created new circuit breaker: {name}")
            
            return self._breakers[name]
    
    def call_with_breaker(self, breaker_name: str, func: Callable, 
                         config: Optional[CircuitBreakerConfig] = None,
                         *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.
        
        Args:
            breaker_name: Name of the circuit breaker to use
            func: Function to execute
            config: Circuit breaker configuration (for new breakers)
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
        """
        breaker = self.get_breaker(breaker_name, config)
        return breaker.call(func, *args, **kwargs)
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all circuit breakers.
        
        Returns:
            Dict mapping breaker names to their statistics
        """
        with self._lock:
            stats = {}
            for name, breaker in self._breakers.items():
                breaker_stats = breaker.get_stats()
                stats[name] = {
                    'state': breaker.get_state().value,
                    'total_requests': breaker_stats.total_requests,
                    'successful_requests': breaker_stats.successful_requests,
                    'failed_requests': breaker_stats.failed_requests,
                    'rejected_requests': breaker_stats.rejected_requests,
                    'state_changes': breaker_stats.state_changes,
                    'last_failure_time': breaker_stats.last_failure_time,
                    'last_success_time': breaker_stats.last_success_time,
                    'current_timeout': breaker_stats.current_timeout,
                    'success_rate': (
                        breaker_stats.successful_requests / breaker_stats.total_requests 
                        if breaker_stats.total_requests > 0 else 0
                    )
                }
            return stats
    
    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
            logger.info("Reset all circuit breakers")
    
    def get_health_status(self) -> Dict[str, Any]:
        """
        Get overall health status of all circuit breakers.
        
        Returns:
            Dict containing health information
        """
        stats = self.get_all_stats()
        
        total_breakers = len(stats)
        open_breakers = sum(1 for s in stats.values() if s['state'] == 'open')
        half_open_breakers = sum(1 for s in stats.values() if s['state'] == 'half_open')
        
        overall_health = "healthy"
        if open_breakers > 0:
            overall_health = "degraded" if open_breakers < total_breakers else "unhealthy"
        elif half_open_breakers > 0:
            overall_health = "recovering"
        
        return {
            'overall_health': overall_health,
            'total_breakers': total_breakers,
            'closed_breakers': total_breakers - open_breakers - half_open_breakers,
            'half_open_breakers': half_open_breakers,
            'open_breakers': open_breakers,
            'breaker_details': stats
        }


# Global circuit breaker manager instance
_circuit_breaker_manager = CircuitBreakerManager()


def get_circuit_breaker_manager() -> CircuitBreakerManager:
    """Get the global circuit breaker manager instance."""
    return _circuit_breaker_manager


def with_circuit_breaker(breaker_name: str, config: Optional[CircuitBreakerConfig] = None):
    """
    Decorator to add circuit breaker protection to a function.
    
    Args:
        breaker_name: Name of the circuit breaker
        config: Circuit breaker configuration
        
    Returns:
        Decorated function
    """
    def decorator(func: Callable) -> Callable:
        def wrapper(*args, **kwargs):
            return _circuit_breaker_manager.call_with_breaker(
                breaker_name, func, config, *args, **kwargs
            )
        return wrapper
    return decorator
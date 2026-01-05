"""
Circuit breaker implementations for external service calls.

Circuit breaker states:
- CLOSED: Normal operation, requests pass through
- OPEN: Failures exceeded threshold, requests fail fast
- HALF_OPEN: Testing if service recovered

Default config: 5 failures to open, 30s recovery, 2 successes to close
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional

from loguru import logger


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for a circuit breaker."""
    name: str = "default"
    failure_threshold: int = 5       # Failures before opening
    recovery_timeout: float = 30.0   # Seconds before half-open
    success_threshold: int = 2       # Successes to close from half-open


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open and rejecting requests."""
    pass


@dataclass
class CircuitBreaker:
    """
    Circuit breaker for protecting external service calls.

    Usage:
        circuit = get_circuit_breaker("pipecat_cloud", config)
        try:
            result = await circuit.call(async_function, arg1, arg2)
        except CircuitOpenError:
            # Handle service unavailable
            pass
    """
    config: CircuitBreakerConfig
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute a function with circuit breaker protection.

        Args:
            func: Async function to call
            *args, **kwargs: Arguments to pass to the function

        Returns:
            Result of the function call

        Raises:
            CircuitOpenError: If circuit is open
            Exception: If the function raises and circuit stays closed
        """
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    logger.info(f"Circuit '{self.config.name}' entering half-open state")
                else:
                    raise CircuitOpenError(
                        f"Circuit '{self.config.name}' is open. "
                        f"Will retry after {self._time_until_retry():.1f}s"
                    )

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure(e)
            raise

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to try a recovery."""
        if self.last_failure_time is None:
            return True
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.config.recovery_timeout

    def _time_until_retry(self) -> float:
        """Get seconds until circuit can be retried."""
        if self.last_failure_time is None:
            return 0.0
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return max(0.0, self.config.recovery_timeout - elapsed)

    async def _on_success(self):
        """Handle successful call."""
        async with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.config.success_threshold:
                    self.state = CircuitState.CLOSED
                    self.failure_count = 0
                    logger.info(f"Circuit '{self.config.name}' closed after recovery")
            elif self.state == CircuitState.CLOSED:
                # Reset failure count on success
                self.failure_count = 0

    async def _on_failure(self, error: Exception):
        """Handle failed call."""
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()

            if self.state == CircuitState.HALF_OPEN:
                # Failed during recovery attempt, reopen
                self.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit '{self.config.name}' reopened after half-open failure: {error}"
                )
            elif self.failure_count >= self.config.failure_threshold:
                # Threshold exceeded, open circuit
                self.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit '{self.config.name}' opened after {self.failure_count} failures"
                )

    def get_status(self) -> Dict[str, Any]:
        """Get current circuit breaker status."""
        return {
            "name": self.config.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "time_until_retry": self._time_until_retry() if self.state == CircuitState.OPEN else 0,
        }


# Global registry of circuit breakers
_circuit_breakers: Dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str, config: Optional[CircuitBreakerConfig] = None) -> CircuitBreaker:
    """
    Get or create a circuit breaker by name.

    Args:
        name: Unique identifier for the circuit breaker
        config: Optional configuration (used only on first call for this name)

    Returns:
        CircuitBreaker instance
    """
    if name not in _circuit_breakers:
        if config is None:
            config = CircuitBreakerConfig(name=name)
        else:
            config.name = name
        _circuit_breakers[name] = CircuitBreaker(config=config)
    return _circuit_breakers[name]


def get_all_circuit_statuses() -> Dict[str, Dict[str, Any]]:
    """Get status of all registered circuit breakers."""
    return {name: cb.get_status() for name, cb in _circuit_breakers.items()}


def reset_circuit(name: str) -> bool:
    """
    Manually reset a circuit breaker to closed state.

    Args:
        name: Name of the circuit breaker to reset

    Returns:
        True if reset, False if circuit not found
    """
    if name in _circuit_breakers:
        cb = _circuit_breakers[name]
        cb.state = CircuitState.CLOSED
        cb.failure_count = 0
        cb.success_count = 0
        logger.info(f"Circuit '{name}' manually reset to closed")
        return True
    return False


def reset_all_circuits():
    """Reset all circuit breakers - useful for testing."""
    _circuit_breakers.clear()

#!/usr/bin/env python3
"""
Retry utilities with exponential backoff for API calls.
Handles rate limits, network timeouts, and transient failures.
"""

import time
import logging
from functools import wraps
from typing import Callable, Optional, Tuple, Any
import requests

logger = logging.getLogger(__name__)


class RetryConfig:
    """Configuration for retry behavior"""

    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 30.0,
        retry_on_exceptions: Tuple = (requests.exceptions.RequestException,),
        retry_on_status_codes: Tuple = (429, 503, 504),
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retry_on_exceptions = retry_on_exceptions
        self.retry_on_status_codes = retry_on_status_codes


def retry_with_backoff(config: Optional[RetryConfig] = None):
    """
    Decorator for retrying functions with exponential backoff.

    Args:
        config: RetryConfig instance with retry parameters

    Returns:
        Decorated function with retry logic
    """
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, config.max_attempts + 1):
                try:
                    result = func(*args, **kwargs)

                    # Check if result has status_code attribute (like HTTP response)
                    if hasattr(result, "status_code"):
                        if result.status_code in config.retry_on_status_codes:
                            logger.warning(
                                f"Attempt {attempt}/{config.max_attempts}: "
                                f"Received status {result.status_code}, retrying..."
                            )
                            if attempt < config.max_attempts:
                                delay = calculate_backoff(
                                    attempt,
                                    config.base_delay,
                                    config.backoff_factor,
                                    config.max_delay,
                                )
                                time.sleep(delay)
                                continue

                    # Success - return result
                    if attempt > 1:
                        logger.info(f"Function {func.__name__} succeeded on attempt {attempt}")
                    return result

                except config.retry_on_exceptions as e:
                    last_exception = e
                    logger.warning(
                        f"Attempt {attempt}/{config.max_attempts}: "
                        f"{type(e).__name__}: {str(e)[:100]}"
                    )

                    if attempt < config.max_attempts:
                        delay = calculate_backoff(
                            attempt, config.base_delay, config.backoff_factor, config.max_delay
                        )

                        # Extract rate limit info if available
                        rate_limit_info = ""
                        if hasattr(e, "response") and e.response is not None:
                            headers = e.response.headers
                            if "Retry-After" in headers:
                                retry_after = int(headers["Retry-After"])
                                delay = max(delay, retry_after)
                                rate_limit_info = f" (rate limit: {retry_after}s)"

                        logger.info(f"Waiting {delay:.1f}s before retry{rate_limit_info}")
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"Function {func.__name__} failed after {config.max_attempts} attempts"
                        )
                        raise

                except Exception as e:
                    # Non-retryable exception
                    logger.error(f"Non-retryable error in {func.__name__}: {type(e).__name__}: {e}")
                    raise

            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            else:
                raise RuntimeError(f"Function {func.__name__} failed unexpectedly")

        return wrapper

    return decorator


def calculate_backoff(
    attempt: int, base_delay: float, backoff_factor: float, max_delay: float
) -> float:
    """
    Calculate exponential backoff delay with jitter.

    Args:
        attempt: Current attempt number (1-indexed)
        base_delay: Base delay in seconds
        backoff_factor: Multiplier for each attempt
        max_delay: Maximum delay in seconds

    Returns:
        Delay in seconds
    """
    import random

    # Exponential backoff: base_delay * (backoff_factor ^ (attempt-1))
    delay = base_delay * (backoff_factor ** (attempt - 1))

    # Add jitter (±20%) to avoid thundering herd
    jitter = random.uniform(0.8, 1.2)
    delay *= jitter

    # Cap at max_delay
    delay = min(delay, max_delay)

    return delay


def retry_api_call(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    **kwargs,
) -> Any:
    """
    Convenience function for retrying API calls.

    Args:
        func: Function to call
        *args: Positional arguments for func
        max_attempts: Maximum number of attempts
        base_delay: Base delay in seconds
        backoff_factor: Exponential backoff factor
        **kwargs: Keyword arguments for func

    Returns:
        Result of successful function call
    """
    config = RetryConfig(
        max_attempts=max_attempts, base_delay=base_delay, backoff_factor=backoff_factor
    )

    @retry_with_backoff(config)
    def wrapped_func():
        return func(*args, **kwargs)

    return wrapped_func()


# Coinbase-specific retry configuration
COINBASE_RETRY_CONFIG = RetryConfig(
    max_attempts=3,
    base_delay=2.0,  # Longer base delay for Coinbase
    backoff_factor=2.0,
    max_delay=60.0,  # Coinbase rate limits can be longer
    retry_on_status_codes=(429, 503, 504),
    retry_on_exceptions=(
        requests.exceptions.RequestException,
        ConnectionError,
        TimeoutError,
    ),
)


def coinbase_retry(func: Callable):
    """
    Specialized decorator for Coinbase API calls.
    """
    return retry_with_backoff(COINBASE_RETRY_CONFIG)(func)


if __name__ == "__main__":
    # Test the retry logic
    import random

    logging.basicConfig(level=logging.INFO)

    @retry_with_backoff(RetryConfig(max_attempts=3, base_delay=0.1))
    def unreliable_function(success_on_attempt: int = 3):
        """Test function that fails until specified attempt"""
        unreliable_function.attempts = getattr(unreliable_function, "attempts", 0) + 1
        current_attempt = unreliable_function.attempts

        if current_attempt < success_on_attempt:
            raise ConnectionError(f"Simulated failure on attempt {current_attempt}")

        return f"Success on attempt {current_attempt}"

    # Test successful retry
    print("Testing retry logic...")
    unreliable_function.attempts = 0
    result = unreliable_function(success_on_attempt=2)
    print(f"Result: {result}")

    # Test ultimate failure
    print("\nTesting failure case...")
    unreliable_function.attempts = 0
    try:
        unreliable_function(success_on_attempt=5)  # Will fail after 3 attempts
    except Exception as e:
        print(f"Expected failure: {type(e).__name__}: {e}")

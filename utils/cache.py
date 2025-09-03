import time
from functools import wraps
from typing import Any, Callable, Dict, Tuple, FrozenSet

def ttl_cache(seconds: int = (30 * 24 * 3600)):
    """
    Simple decorator to add time‑to‑live (TTL) caching to a function.

    Each unique combination of positional and keyword arguments is cached
    alongside the timestamp when it was stored.  When the decorated
    function is called, the cache is checked and the stored value is
    returned if it has not expired.  Otherwise, the function is
    executed and its result cached.

    Parameters
    ----------
    seconds : int, optional
        Number of seconds to keep a cached result.  Defaults to 3600
        (one hour).

    Returns
    -------
    Callable
        A wrapper function with TTL caching applied.
    """
    def decorator(fn: Callable):
        cache: Dict[Tuple[Tuple[Any, ...], FrozenSet[Tuple[str, Any]]], Tuple[Any, float]] = {}
        @wraps(fn)
        def wrapper(*args, **kwargs):
            # Create a hashable key from args and kwargs
            key = (args, frozenset(kwargs.items()))
            now = time.time()

            # Check existing cached value
            if key in cache:
                val, ts = cache[key]
                if now - ts < seconds:
                    return val
                
            # Compute and cache result
            result = fn(*args, **kwargs)
            cache[key] = (result, now)
            return result
        return wrapper
    return decorator
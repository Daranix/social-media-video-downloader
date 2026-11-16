from typing import Any, Optional, cast
from typing_extensions import Literal
from src.cache.cache_store import CacheStore

CacheStrategy = Literal['in-memory', 'simple-dict']

class CacheManager:
    """
    Cache manager implementing the Strategy pattern.
    Allows switching between different cache implementations at runtime.
    """
    
    def __init__(self, strategy: CacheStrategy):
        """
        Initialize the cache manager with a specific cache strategy.

        Args:
            strategy: An instance implementing CacheInterface.
        """
        if strategy == 'in-memory':
            from src import config
            from src.cache.in_memory_cache import InMemoryCache
            raw_interval = config.CACHE_CLEANUP_INTERVAL
            cleanup_interval = max(1, 300 if raw_interval is None else int(raw_interval))
            self._strategy = InMemoryCache(cleanup_interval=cleanup_interval)
        else:
            raise ValueError(f"Invalid cache strategy: {strategy}")
    
    def get(self, key: str) -> Optional[Any]:
        """Delegate to the current strategy."""
        return self._strategy.get(key)
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Delegate to the current strategy."""
        self._strategy.set(key, value, ttl)
    
    def delete(self, key: str) -> bool:
        """Delegate to the current strategy."""
        return self._strategy.delete(key)
    
    def clear(self) -> None:
        """Delegate to the current strategy."""
        self._strategy.clear()
    
    def exists(self, key: str) -> bool:
        """Delegate to the current strategy."""
        return self._strategy.exists(key)
    
    def get_ttl(self, key: str) -> Optional[int]:
        """Delegate to the current strategy."""
        return self._strategy.get_ttl(key)

    def size(self) -> int:
        """Return the number of items in the cache."""
        return self._strategy.size()
    def keys(self) -> list[str]:
        """Return a list of keys currently in the cache."""
        return self._strategy.keys()
        
    def values(self) -> list[Any]:
        """Return a list of values currently in the cache."""
        return self._strategy.values()
    
    def items(self) -> list[tuple[str, Any]]:
        """Return a list of (key, value) tuples currently in the cache."""
        return self._strategy.items()

    def pop(self, key: str) -> Optional[Any]:
        """Atomically remove and return a non-expired value, or None."""
        pop_fn = getattr(self._strategy, "pop", None)
        if callable(pop_fn):
            return pop_fn(key)
        value = self._strategy.get(key)
        if value is not None:
            self._strategy.delete(key)
        return value

    def snapshot_entries(self) -> list[tuple[str, Any, Optional[float], Optional[float]]]:
        """Return an atomic snapshot of active entries with age and remaining TTL."""
        snap_fn = getattr(self._strategy, "snapshot_entries", None)
        if callable(snap_fn):
            return cast(list[tuple[str, Any, Optional[float], Optional[float]]], snap_fn())
        return [(k, v, None, None) for k, v in self._strategy.items()]
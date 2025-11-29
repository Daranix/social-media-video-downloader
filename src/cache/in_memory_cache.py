import threading
from typing import Any, Optional
import time

from src.cache.cache_store import CacheStore


class InMemoryCache(CacheStore):
    """Thread-safe in-memory cache implementation with TTL support."""
    
    def __init__(self, cleanup_interval: int = 60):
        """
        Initialize the cache.
        
        Args:
            cleanup_interval: Interval in seconds to clean expired items.
        """
        self._data = {}
        self._expiry = {}
        self._lock = threading.RLock()
        self._cleanup_interval = cleanup_interval
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired,
            daemon=True
        )
        self._cleanup_thread.start()
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL in seconds."""
        with self._lock:
            self._data[key] = value
            if ttl is not None:
                self._expiry[key] = time.time() + ttl
            else:
                self._expiry.pop(key, None)
    
    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value if it exists and hasn't expired."""
        with self._lock:
            if key not in self._data:
                return None
            
            if self._is_expired(key):
                self._remove_expired(key)
                return None
            
            return self._data[key]
    
    def delete(self, key: str) -> bool:
        """Delete a key from the cache."""
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._expiry.pop(key, None)
                return True
            return False
    
    def clear(self) -> None:
        """Clear all items from the cache."""
        with self._lock:
            self._data.clear()
            self._expiry.clear()
    
    def exists(self, key: str) -> bool:
        """Check if a key exists and hasn't expired."""
        with self._lock:
            if key not in self._data:
                return False
            
            if self._is_expired(key):
                self._remove_expired(key)
                return False
            
            return True
    
    def get_ttl(self, key: str) -> Optional[int]:
        """Get remaining TTL in seconds, or None if no TTL or key doesn't exist."""
        with self._lock:
            if key not in self._data:
                return None
            
            if key not in self._expiry:
                return None
            
            remaining = int(self._expiry[key] - time.time())
            return max(0, remaining)
    
    def _is_expired(self, key: str) -> bool:
        """Check if a key has expired."""
        if key not in self._expiry:
            return False
        return time.time() >= self._expiry[key]
    
    def _remove_expired(self, key: str) -> None:
        """Remove an expired key."""
        self._data.pop(key, None)
        self._expiry.pop(key, None)
    
    def _cleanup_expired(self) -> None:
        """Periodically clean up expired items."""
        while True:
            time.sleep(self._cleanup_interval)
            with self._lock:
                expired_keys = [
                    key for key in list(self._expiry.keys())
                    if self._is_expired(key)
                ]
                for key in expired_keys:
                    self._remove_expired(key)
    
    def size(self) -> int:
        """Return the number of items in the cache."""
        with self._lock:
            return len(self._data)

    def keys(self) -> list[str]:
        """Return a list of keys currently in the cache."""
        with self._lock:
            return list(self._data.keys())
        
    def values(self) -> list[Any]:
        """Return a list of values currently in the cache."""
        with self._lock:
            return list(self._data.values())
    
    def items(self) -> list[tuple[str, Any]]:
        """Return a list of (key, value) tuples currently in the cache."""
        with self._lock:
            return list(self._data.items())
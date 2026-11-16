import threading
from typing import Any, Optional
import time

from src.cache.cache_store import CacheStore


class InMemoryCache(CacheStore):
    """Thread-safe in-memory cache implementation with TTL support and bounded capacity."""

    def __init__(self, cleanup_interval: int = 60, max_entries: int = 256):
        """
        Initialize the cache.

        Args:
            cleanup_interval: Interval in seconds to clean expired items (clamped >= 1).
            max_entries: Maximum entries retained in memory (clamped >= 1).
        """
        self._data: dict[str, Any] = {}
        self._expiry: dict[str, float] = {}
        self._created_at: dict[str, float] = {}
        self._lock = threading.RLock()
        self._cleanup_interval = max(1, int(cleanup_interval))
        self._max_entries = max(1, int(max_entries))
        self._cleanup_thread = threading.Thread(
            target=self._cleanup_expired,
            daemon=True,
        )
        self._cleanup_thread.start()

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value with optional TTL in seconds."""
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            if key in self._data:
                self._data.pop(key, None)
            elif len(self._data) >= self._max_entries:
                oldest_key = next(iter(self._data))
                self._remove_expired(oldest_key)

            self._data[key] = value
            self._created_at[key] = now
            if ttl is not None:
                self._expiry[key] = now + ttl
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
        """Delete a non-expired key from the cache."""
        with self._lock:
            self._prune_expired_locked()
            if key in self._data:
                self._remove_expired(key)
                return True
            return False

    def pop(self, key: str) -> Optional[Any]:
        """Atomically remove and return a non-expired value, or None."""
        with self._lock:
            self._prune_expired_locked()
            if key not in self._data:
                return None
            value = self._data.get(key)
            self._remove_expired(key)
            return value

    def clear(self) -> None:
        """Clear all items from the cache."""
        with self._lock:
            self._data.clear()
            self._expiry.clear()
            self._created_at.clear()

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

            if self._is_expired(key):
                self._remove_expired(key)
                return None

            if key not in self._expiry:
                return None

            remaining = int(self._expiry[key] - time.monotonic())
            return max(0, remaining)

    def _is_expired(self, key: str, now: Optional[float] = None) -> bool:
        """Check if a key has expired."""
        if key not in self._expiry:
            return False
        current = time.monotonic() if now is None else now
        return current >= self._expiry[key]

    def _remove_expired(self, key: str) -> None:
        """Remove a key and its metadata."""
        self._data.pop(key, None)
        self._expiry.pop(key, None)
        self._created_at.pop(key, None)

    def _prune_expired_locked(self, now: Optional[float] = None) -> None:
        """Prune all expired keys while caller holds self._lock."""
        current = time.monotonic() if now is None else now
        expired_keys = [
            key for key in list(self._expiry.keys())
            if self._is_expired(key, current)
        ]
        for key in expired_keys:
            self._remove_expired(key)

    def _cleanup_expired(self) -> None:
        """Periodically clean up expired items."""
        while True:
            time.sleep(self._cleanup_interval)
            with self._lock:
                self._prune_expired_locked()

    def size(self) -> int:
        """Return the number of active items in the cache."""
        with self._lock:
            self._prune_expired_locked()
            return len(self._data)

    def keys(self) -> list[str]:
        """Return a list of active keys currently in the cache."""
        with self._lock:
            self._prune_expired_locked()
            return list(self._data.keys())

    def values(self) -> list[Any]:
        """Return a list of active values currently in the cache."""
        with self._lock:
            self._prune_expired_locked()
            return list(self._data.values())

    def items(self) -> list[tuple[str, Any]]:
        """Return a list of active (key, value) tuples currently in the cache."""
        with self._lock:
            self._prune_expired_locked()
            return list(self._data.items())

    def snapshot_entries(self) -> list[tuple[str, Any, Optional[float], Optional[float]]]:
        """Return an atomic snapshot of active (key, value, age_seconds, expires_in_seconds)."""
        now = time.monotonic()
        with self._lock:
            self._prune_expired_locked(now)
            entries: list[tuple[str, Any, Optional[float], Optional[float]]] = []
            for key, value in self._data.items():
                created_at = self._created_at.get(key)
                age_seconds = max(0.0, round(now - created_at, 2)) if created_at is not None else None
                expiry = self._expiry.get(key)
                expires_in = max(0.0, round(expiry - now, 2)) if expiry is not None else None
                entries.append((key, value, age_seconds, expires_in))
            return entries

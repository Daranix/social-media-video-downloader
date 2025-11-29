from abc import ABC, abstractmethod
from typing import Any, Optional

class CacheStore(ABC):
    """Abstract base class defining the cache interface."""
    
    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Retrieve a value from the cache."""
        pass
    
    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """Store a value in the cache with optional TTL in seconds."""
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a key from the cache."""
        pass
    
    @abstractmethod
    def clear(self) -> None:
        """Clear all items from the cache."""
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists and is not expired."""
        pass
    
    @abstractmethod
    def get_ttl(self, key: str) -> Optional[int]:
        """Get remaining TTL in seconds, or None if no TTL."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Return the number of items in the cache."""
        pass

    @abstractmethod
    def keys(self) -> list[str]:
        """Return a list of keys currently in the cache."""
        pass
    
    @abstractmethod
    def values(self) -> list[Any]:
        """Return a list of values currently in the cache."""
        pass
    
    @abstractmethod
    def items(self) -> list[tuple[str, Any]]:
        """Return a list of (key, value) tuples currently in the cache."""
        pass
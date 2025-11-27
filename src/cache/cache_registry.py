import threading
from src.cache.cache_manager import CacheManager, CacheStrategy


class CacheRegistry:
    """
    Registry for managing multiple named cache instances globally.
    Allows creating, accessing, and managing multiple cache instances across the app.
    """
    
    _instances: dict[str, CacheManager] = {}
    _lock = threading.Lock()
    _default_cache = 'default'
    
    @classmethod
    def create(cls, name: str, strategy: CacheStrategy) -> CacheManager:
        """
        Create and register a new cache instance.
        
        Args:
            name: Unique identifier for this cache instance.
            strategy: The cache strategy to use.
            
        Returns:
            The created CacheManager instance.
            
        Raises:
            ValueError: If cache with this name already exists.
        """
        with cls._lock:
            if name in cls._instances:
                raise ValueError(f"Cache '{name}' already exists. Use get() or delete() first.")
            
            cache = CacheManager(strategy)
            cls._instances[name] = cache
            return cache
    
    @classmethod
    def get(cls, name: str = 'default') -> CacheManager:
        """
        Get a cache instance by name.
        
        Args:
            name: The cache instance name. Defaults to 'default'.
            
        Returns:
            The CacheManager instance.
            
        Raises:
            KeyError: If cache with this name doesn't exist.
        """
        if name not in cls._instances:
            available = ', '.join(cls._instances.keys()) or 'none'
            raise KeyError(
                f"Cache '{name}' not found. "
                f"Available caches: {available}"
            )
        return cls._instances[name]
    
    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if a cache instance exists."""
        return name in cls._instances
    
    @classmethod
    def delete(cls, name: str) -> bool:
        """
        Delete a cache instance.
        
        Args:
            name: The cache instance name.
            
        Returns:
            True if deleted, False if it didn't exist.
        """
        with cls._lock:
            if name in cls._instances:
                cls._instances[name].clear()
                del cls._instances[name]
                return True
            return False
    
    @classmethod
    def clear_all(cls) -> None:
        """Clear all cache instances."""
        with cls._lock:
            for cache in cls._instances.values():
                cache.clear()
            cls._instances.clear()
    
    @classmethod
    def list_caches(cls) -> list[str]:
        """List all registered cache instances."""
        return list(cls._instances.keys())
    
    @classmethod
    def set_default(cls, name: str) -> None:
        """Set the default cache instance."""
        if not cls.exists(name):
            raise KeyError(f"Cache '{name}' does not exist.")
        cls._default_cache = name
    
    @classmethod
    def get_default(cls) -> CacheManager:
        """Get the default cache instance."""
        return cls.get(cls._default_cache)
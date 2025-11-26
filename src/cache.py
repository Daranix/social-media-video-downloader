import time
from diskcache import Cache

from .config import CACHE_CLEANUP_INTERVAL, CACHE_TTL_SECONDS

# Initialize cache - in-memory by default (None). Swap to disk path to persist.
video_cache: Cache = Cache(None)
last_cleanup_time = time.time()


def cleanup_expired_cache() -> None:
    """Remove expired entries from cache based on TTL."""
    global last_cleanup_time
    current_time = time.time()

    # Only cleanup if enough time has passed
    if current_time - last_cleanup_time < CACHE_CLEANUP_INTERVAL:
        return

    last_cleanup_time = current_time
    expired_hashes = []

    # Use snapshot list to avoid runtime change issues
    for video_hash in list(video_cache):
        cached_data = video_cache.get(video_hash, {})
        timestamp = cached_data.get("timestamp", current_time)
        age = current_time - timestamp

        if age > CACHE_TTL_SECONDS:
            expired_hashes.append(video_hash)

    # Delete expired entries
    for video_hash in expired_hashes:
        try:
            del video_cache[video_hash]
            print(f"[Cache] Removed expired entry: {video_hash}")
        except Exception as e:
            print(f"[Cache] Error removing {video_hash}: {str(e)}")

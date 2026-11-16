import pytest

from src import config, ytdl_ops
from src.cache.cache_registry import CacheRegistry


@pytest.fixture(autouse=True)
def isolated_app_state(tmp_path, monkeypatch):
    """Isolate TEMP_DIR, config overrides, cache, and temp-dir cleanup state per test."""
    monkeypatch.setattr(config, "CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(config, "CACHE_CLEANUP_INTERVAL", 300)
    monkeypatch.setattr(config, "YTDLP_MAX_CONCURRENT_JOBS", 4)
    monkeypatch.setattr(config, "TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(config, "COOKIES_FILE", None)
    monkeypatch.setattr(config, "PROXY_URL", None)
    monkeypatch.setattr(config, "YTDLP_IMPERSONATE", None)
    monkeypatch.setattr(config, "YTDLP_SLEEP_REQUESTS", 0.5)
    monkeypatch.setattr(config, "YTDLP_EXTRACTOR_RETRIES", 3)

    ytdl_ops.reset_cleanup_state()
    if CacheRegistry.exists("default"):
        CacheRegistry.get_default().clear()
    yield
    ytdl_ops.reset_cleanup_state()
    if CacheRegistry.exists("default"):
        CacheRegistry.get_default().clear()

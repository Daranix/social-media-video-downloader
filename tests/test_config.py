import importlib
import tempfile

import dotenv
import pytest

from src import config


_ENV_KEYS = (
    "CACHE_TTL_SECONDS",
    "CACHE_CLEANUP_INTERVAL",
    "TEMP_DIR",
    "COOKIES_FILE",
    "PROXY_URL",
    "YTDLP_IMPERSONATE",
    "YTDLP_SLEEP_REQUESTS",
    "YTDLP_EXTRACTOR_RETRIES",
    "YTDLP_MAX_CONCURRENT_JOBS",
)


@pytest.fixture
def reload_config(monkeypatch):
    """Reload src.config with controlled env vars (ignoring developer .env) and restore afterward."""
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    def _clear():
        for key in _ENV_KEYS:
            monkeypatch.delenv(key, raising=False)

    _clear()

    def _reload(**env_vars: str):
        _clear()
        for key, value in env_vars.items():
            monkeypatch.setenv(key, value)
        return importlib.reload(config)

    yield _reload

    _clear()
    importlib.reload(config)


def test_config_defaults_when_env_unset(reload_config):
    cfg = reload_config()
    assert cfg.CACHE_TTL_SECONDS == 3600
    assert cfg.CACHE_CLEANUP_INTERVAL == 300
    assert cfg.TEMP_DIR == tempfile.gettempdir()
    assert cfg.COOKIES_FILE is None
    assert cfg.PROXY_URL is None
    assert cfg.YTDLP_IMPERSONATE is None
    assert cfg.YTDLP_SLEEP_REQUESTS == 0.5
    assert cfg.YTDLP_EXTRACTOR_RETRIES == 3
    assert cfg.YTDLP_MAX_CONCURRENT_JOBS == 4


def test_config_normalizes_empty_and_whitespace_strings_to_none(reload_config):
    cfg = reload_config(
        CACHE_TTL_SECONDS="   ",
        CACHE_CLEANUP_INTERVAL="",
        TEMP_DIR="  ",
        COOKIES_FILE="   ",
        PROXY_URL="",
        YTDLP_IMPERSONATE=" \t\n ",
        YTDLP_SLEEP_REQUESTS="  ",
        YTDLP_EXTRACTOR_RETRIES="",
        YTDLP_MAX_CONCURRENT_JOBS="  ",
    )
    assert cfg.CACHE_TTL_SECONDS == 3600
    assert cfg.CACHE_CLEANUP_INTERVAL == 300
    assert cfg.TEMP_DIR == tempfile.gettempdir()
    assert cfg.COOKIES_FILE is None
    assert cfg.PROXY_URL is None
    assert cfg.YTDLP_IMPERSONATE is None
    assert cfg.YTDLP_SLEEP_REQUESTS is None
    assert cfg.YTDLP_EXTRACTOR_RETRIES is None
    assert cfg.YTDLP_MAX_CONCURRENT_JOBS == 4


def test_config_strips_valid_values_and_temp_dir(reload_config, tmp_path):
    custom_tmp = str(tmp_path / "custom_tmp")
    cfg = reload_config(
        CACHE_TTL_SECONDS=" 600 ",
        CACHE_CLEANUP_INTERVAL=" 120 ",
        TEMP_DIR=f"  {custom_tmp}  ",
        COOKIES_FILE="  /app/cookies.txt  ",
        PROXY_URL="  socks5://127.0.0.1:1080  ",
        YTDLP_IMPERSONATE="  chrome  ",
        YTDLP_SLEEP_REQUESTS=" 1.25 ",
        YTDLP_EXTRACTOR_RETRIES=" 5 ",
        YTDLP_MAX_CONCURRENT_JOBS=" 8 ",
    )
    assert cfg.CACHE_TTL_SECONDS == 600
    assert cfg.CACHE_CLEANUP_INTERVAL == 120
    assert cfg.TEMP_DIR == custom_tmp
    assert cfg.COOKIES_FILE == "/app/cookies.txt"
    assert cfg.PROXY_URL == "socks5://127.0.0.1:1080"
    assert cfg.YTDLP_IMPERSONATE == "chrome"
    assert cfg.YTDLP_SLEEP_REQUESTS == 1.25
    assert cfg.YTDLP_EXTRACTOR_RETRIES == 5
    assert cfg.YTDLP_MAX_CONCURRENT_JOBS == 8


def test_config_allows_zero_values(reload_config):
    cfg = reload_config(
        CACHE_TTL_SECONDS="0",
        CACHE_CLEANUP_INTERVAL="0",
        YTDLP_SLEEP_REQUESTS="0",
        YTDLP_EXTRACTOR_RETRIES="0",
    )
    assert cfg.CACHE_TTL_SECONDS == 0
    assert cfg.CACHE_CLEANUP_INTERVAL == 0
    assert cfg.YTDLP_SLEEP_REQUESTS == 0.0
    assert cfg.YTDLP_EXTRACTOR_RETRIES == 0


@pytest.mark.parametrize("bad_value", ["-0.1", "-1", "nan", "inf", "not-a-number"])
def test_config_rejects_invalid_sleep_requests(reload_config, bad_value):
    with pytest.raises(ValueError, match="YTDLP_SLEEP_REQUESTS"):
        reload_config(YTDLP_SLEEP_REQUESTS=bad_value)


@pytest.mark.parametrize("bad_value", ["-1", "1.5", "not-an-int"])
def test_config_rejects_invalid_extractor_retries(reload_config, bad_value):
    with pytest.raises(ValueError, match="YTDLP_EXTRACTOR_RETRIES"):
        reload_config(YTDLP_EXTRACTOR_RETRIES=bad_value)


@pytest.mark.parametrize("bad_value", ["-1", "1.5", "abc"])
def test_config_rejects_invalid_cache_ttl(reload_config, bad_value):
    with pytest.raises(ValueError, match="CACHE_TTL_SECONDS"):
        reload_config(CACHE_TTL_SECONDS=bad_value)


@pytest.mark.parametrize("bad_value", ["0", "-1", "1.5", "abc"])
def test_config_rejects_invalid_max_concurrent_jobs(reload_config, bad_value):
    with pytest.raises(ValueError, match="YTDLP_MAX_CONCURRENT_JOBS"):
        reload_config(YTDLP_MAX_CONCURRENT_JOBS=bad_value)

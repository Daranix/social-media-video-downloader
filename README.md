# Social Media Video Downloader

Small FastAPI service that uses `yt-dlp` to extract metadata and download videos from platforms supported by `yt-dlp`.

Downloads are written into an isolated temporary directory per request and are scheduled for removal after a configurable TTL to avoid accumulating files on disk.

**Contents**
- `src/api.py` — FastAPI application and HTTP endpoints
- `src/ytdl_ops.py` — yt-dlp wrapper: metadata extraction, download, error mapping, and cleanup handling
- `src/utils.py` — helpers (hashing, ID extraction, platform detection)
- `src/cache/` — cache implementations and registry
- `src/models/` — Pydantic models and typed interfaces
- `src/config.py` — configuration defaults and environment loading
- `main.py` — quick runner for local development

**Quick Start (local)**

Prereqs:
- Python 3.13+
- `ffmpeg` installed on the host (required by `yt-dlp` for merging/format conversions)

Install dependencies (using `uv`):

```powershell
pip install uv
uv sync
```

Run the app:

```powershell
uv run uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
# Or run via main.py:
uv run main.py
```

Debugging in VS Code:
- This project includes a `.vscode/launch.json` configuration so you can run the FastAPI app in the VS Code debugger (use the Run and Debug pane).

Open the interactive docs at: `http://localhost:8000/docs`

Docker (build & run):

```powershell
docker build -t sm-video-downloader .
docker run -p 8000:8000 sm-video-downloader
```

Note: The Docker image runs with `--workers 1` because the default cache (`InMemoryCache`) is process-local in-memory.

**Configuration**
- Defaults are loaded in `src/config.py` and can be overridden using environment variables or a `.env` file (see `.env.example`, loaded via `python-dotenv`).
- `TEMP_DIR` *(optional)* — directory used for per-request temporary working directories (`smvd_*` and `smvd_cookies_*`). Empty/whitespace defaults to the system temporary directory.
- `CACHE_TTL_SECONDS` *(default: `3600`)* — how long cached entries and downloaded `smvd_*` temp directories remain before removal. When set to `0`, entries are not cached, the response streams first, and `CleanupFileResponse` removes the `smvd_*` directory immediately in `finally`.
- `CACHE_CLEANUP_INTERVAL` *(default: `300`)* — cache cleanup interval in seconds (`InMemoryCache` and temp-directory sweeper bounds).
- `YTDLP_MAX_CONCURRENT_JOBS` *(default: `4`)* — positive integer bounding concurrent blocking `yt-dlp` operations executed via `asyncio.to_thread`.
- `COOKIES_FILE` *(optional)* — path to a Netscape-format `cookies.txt` file passed to `yt-dlp` via `cookiefile`. Empty/whitespace values are normalized to `None`. Before each operation, the service verifies the file exists and copies it to a writable per-request temporary directory (`0o700` dir / `0o600` file, removed in `finally`), so read-only Docker mounts (`:ro`) will not fail or modify the source file.
- `PROXY_URL` *(optional)* — HTTP, HTTPS, or SOCKS proxy URL (e.g. `http://user:pass@host:port` or `socks5://127.0.0.1:1080`) passed to `yt-dlp` via `proxy`. Empty/whitespace values are normalized to `None`.
- `YTDLP_IMPERSONATE` *(optional, default behavior: `auto`)* — controls `curl-cffi` browser impersonation via `yt-dlp`:
  - `auto` (or unset/empty): enables `ImpersonateTarget()` for Instagram URLs while keeping global `impersonate` unset for YouTube so `player_client: ['default', 'ios', 'android_vr']` is not disrupted.
  - `off`: omits explicit `impersonate` from `yt-dlp` options (note that `yt-dlp`'s built-in Instagram extractor still auto-impersonates internally when `curl-cffi` is installed).
  - Target string (e.g. `chrome`, `chrome-131`, `chrome:windows-10`, `safari`): parsed with `ImpersonateTarget.from_str(...)` and applied to non-YouTube URLs while still protecting YouTube's `player_client` configuration.
- `YTDLP_SLEEP_REQUESTS` *(optional float, default: `0.5`)* — non-negative seconds to sleep between HTTP requests during extraction (`sleep_interval_requests`). Empty/whitespace disables the override (`None`).
- `YTDLP_EXTRACTOR_RETRIES` *(optional int, default: `3`)* — non-negative retry count for known extractor errors (`extractor_retries`). Empty/whitespace uses `yt-dlp`'s default.

**Instagram Anti-Block & Limitations**
- The project installs `yt-dlp[default,curl-cffi]`, which enables TLS/HTTP2 browser impersonation (`curl_cffi`) used by `yt-dlp`'s Instagram extractor.
- Even with impersonation enabled, Instagram aggressively rate-limits or blocks anonymous requests from datacenter/cloud IPs and gates private, age-restricted, story, or follow-only posts behind authentication (`sessionid` cookie).
- When Instagram redirects anonymous requests to the login page due to rate limiting, the API returns HTTP `429`; login-required/private content returns `401`; upstream empty responses or transient blocks return `503`.
- For reliable Instagram extraction in production or Docker, configure `COOKIES_FILE` with an authenticated Netscape `cookies.txt` (mounted read-only, e.g. `-v ./cookies.txt:/app/cookies.txt:ro`) and/or `PROXY_URL` with a residential/clean proxy. Never commit `.env` or cookie files (ignored by `.gitignore` and `.dockerignore`).

**Dependency management (uv)**
- This project declares dependencies in `pyproject.toml` and `uv.lock` and is managed with `uv`.
- Install all dependencies (including `pytest` for development):

```powershell
uv sync
```

- Install production dependencies only (as in `Dockerfile`):

```powershell
uv sync --locked --no-dev
```

**Behavior: temporary files & cleanup**
- Each download uses a unique temporary directory under `TEMP_DIR` (prefix `smvd_`).
- When `CACHE_TTL_SECONDS > 0`, a single daemon sweeper thread removes expired, unleased `smvd_` directories after `CACHE_TTL_SECONDS` (checked against bounded intervals based on `CACHE_CLEANUP_INTERVAL`), retrying failed removals and respecting active streaming leases.
- When `CACHE_TTL_SECONDS <= 0`, `CleanupFileResponse` removes the `smvd_` directory in `finally` immediately after streaming finishes (even on client disconnect or streaming errors).
- Leftover `smvd_*` and `smvd_cookies_*` directories in `TEMP_DIR` are also swept on application startup.
- Cookie copies (`smvd_cookies_*`, `0o700` dir / `0o600` file) are always removed immediately in `finally` at the end of each yt-dlp operation.
- Cleanup is best-effort and will not block request handling.

**API Endpoints (overview)**
- `GET /api/info?url=...` — Extracts video metadata and caches sanitized summary/raw metadata by extracted video ID (`{extractor}_{id}`). Returns `VideoCacheData`.
- `GET /api/cache/info/{video_id}` — Retrieve cached `VideoInfo` metadata for a previously extracted video.
- `GET /api/download?url=...` — Downloads the requested video and returns the file (`video/mp4`).
- `GET /api/cache/download/{video_id}` — Download a video by its cached `video_id` (streams existing file under an active lease or downloads from cached URL).
- `POST /api/download/advanced` — Accepts a JSON `VideoDownloadOptions` body and returns the downloaded file (`application/octet-stream`).
- `GET /api/cache` — List active cached videos with sanitized metadata summaries (`id`, redacted `url`, `info`, `age_seconds`, `expires_in_seconds`).
- `DELETE /api/cache/{video_hash}` — Delete a cache entry by ID and clean up any associated unleased `smvd_*` directory.

See `src/api.py` for full OpenAPI metadata and examples.

**Running tests**

```powershell
uv run pytest -v
```

**Troubleshooting**
- If downloads fail, confirm `ffmpeg` is on PATH and `yt-dlp` is up-to-date.
- Ensure the service has write permissions to `TEMP_DIR` (or set `TEMP_DIR` to a writable location).

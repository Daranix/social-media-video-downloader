# Social Media Video Downloader

Small FastAPI service that uses `yt-dlp` to extract metadata and download videos from platforms supported by `yt-dlp`. Downloads are cached (metadata only) and files are written to the system temporary directory when requested.

Quick start (local):

1. Install dependencies (managed with `uv`).

```powershell
pip install uv
uv sync
```

# Social Media Video Downloader

Small FastAPI service that uses `yt-dlp` to extract metadata and download videos from platforms supported by `yt-dlp`.

Downloads are written into an isolated temporary directory per-request and are scheduled for removal after a configurable TTL to avoid accumulating files on disk.

**Contents**
- `src/api.py` — FastAPI application and HTTP endpoints
- `src/ytdl_ops.py` — yt-dlp wrapper: extract metadata, download and cache handling
- `src/utils.py` — helpers (hashing, id extraction, validation)
- `src/cache/` — cache implementations and registry
- `src/models/` — Pydantic models and typed interfaces
- `src/config.py` — configuration defaults and environment loading
- `main.py` — quick runner for local development

**Quick Start (local)**

Prereqs:
- Python 3.10+
- `ffmpeg` installed on the host (required by `yt-dlp` for merging/format conversions)

Install dependencies (example, using `uv`):

```powershell
pip install uv
uv sync
```

Run the app:

```powershell
uvicorn src.api:app --reload --host 0.0.0.0 --port 8000
# Development (dev mode) using `uv`:
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

**Configuration**
- `TEMP_DIR` and other defaults are defined in `src/config.py`. You can override configuration using environment variables (see `.env` support via `python-dotenv`).
- `CACHE_TTL_SECONDS` controls how long downloaded files remain on disk before the application schedules their removal (default: 3600 seconds).

**Dependency management (uv)**
- This project declares dependencies in `pyproject.toml` and is intended to be managed with `uv` (instead of a `requirements.txt`).
- If you use `uv`, install it first (example):

```powershell
pip install uv
```

- Then install the project dependencies from `pyproject.toml` with:

```powershell
uv install
```

- You can produce a `requirements.txt` (if needed) from your `uv` lock/export commands; consult your `uv` workflow for exact flags (common operations include `uv lock` and `uv export`).

**Behavior: temporary files & cleanup**
- Each download uses a unique temporary directory under the system temp dir (prefix `smvd_`).
- The service schedules a background cleanup of that directory after `CACHE_TTL_SECONDS`. If the app is running under an asyncio loop (typical with FastAPI + Uvicorn), cleanup uses an `asyncio` task; otherwise it falls back to a `threading.Timer`.
- Cleanup is best-effort and will not block request handling.

**API Endpoints (overview)**
- `GET /api/extract?url=...` — Extracts video metadata (title, duration, uploader, platform, video hash). Returns `VideoInfo`.
- `GET /api/download?url=...` — Downloads the requested video and returns the file as binary. Files are written to a temp directory and scheduled for cleanup.
- `POST /api/download/advanced` — Accepts a JSON `VideoDownloadOptions` body with advanced options (format, audio-only, resolution) and returns the binary file.
- `GET /api/info/{video_hash}` — Retrieve cached metadata for a previously extracted video.
- `GET /api/download/{video_hash}` — Download a video by its cache entry (if available).
- `GET /api/cache` — List cache entries and expiration status.

See `src/api.py` for full OpenAPI metadata and examples.

**Development notes**
- The project uses a small cache registry under `src/cache/`. The default cache is in-memory; you can add a persistent store if desired.
- `yt-dlp` options are assembled in `src/ytdl_ops.py`. You can customize output templates and formats there.

**Testing manually**
1. Start the app.
2. Open `http://localhost:8000/docs` and try the `extract` then `download` endpoints.
3. Verify the temporary directory is created under your system temp (e.g., `%TEMP%` on Windows) with prefix `smvd_` and that it is removed after `CACHE_TTL_SECONDS`.

**Troubleshooting**
- If downloads fail, confirm `ffmpeg` is on PATH and `yt-dlp` is up-to-date.
- Ensure the service has write permissions to the system temp directory or set `TEMP_DIR` to a writable location.

If you'd like, I can:
- Run a quick end-to-end test download locally and report results.
- Add a `requirements.txt` or `pyproject.toml` snippet to standardize dependencies.

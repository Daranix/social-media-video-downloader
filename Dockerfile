## Builder: use uv image to provision a managed Python and install dependencies
FROM ghcr.io/astral-sh/uv:bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
ENV UV_PYTHON_INSTALL_DIR=/python
ENV UV_PYTHON_PREFERENCE=only-managed

# Install a managed Python version for the builder
RUN uv python install 3.13

# Install git and ca-certificates for dependencies from git sources
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Use build cache mounts to speed up dependency installs. Bind pyproject.toml and uv.lock so
# buildkit can cache dependency resolution separately from source changes.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --locked --no-install-project --no-dev

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

## Final image: lightweight Debian bookworm-slim
FROM debian:bookworm-slim

# Setup a non-root user
RUN groupadd --system --gid 999 nonroot \
 && useradd --system --gid 999 --uid 999 --create-home nonroot

# Install runtime system deps (ffmpeg for merging/audio extraction, curl for deno)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    unzip \
    && curl -fsSL https://deno.land/install.sh | sh \
    && mv /root/.deno/bin/deno /usr/local/bin/deno \
    && rm -rf /var/lib/apt/lists/*

# Copy the Python installation from the builder
COPY --from=builder /python /python

# Copy the application from the builder (already installed deps in .venv)
COPY --from=builder --chown=nonroot:nonroot /app /app

# Place the virtualenv executables at the front of PATH
ENV PATH="/app/.venv/bin:$PATH"

USER nonroot
WORKDIR /app

ENV PORT=8000
# Run with --workers 1 because the default cache is process-local in-memory.
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port \"${PORT}\" --workers 1"]

# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# libgomp1 is required by some binary wheels (e.g. pypdfium2 on some platforms)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Dependency layer (cached unless pyproject.toml / uv.lock change) ─────────
FROM base AS deps

COPY pyproject.toml uv.lock .python-version ./

# Install all production dependencies into .venv, but not the project itself.
# This layer is cached separately from the source code.
RUN uv sync --frozen --no-dev --no-install-project

# ── Final image ───────────────────────────────────────────────────────────────
FROM base AS final

WORKDIR /app

# Copy pre-built venv
COPY --from=deps /app/.venv /app/.venv

# Copy project files needed for installation
COPY pyproject.toml uv.lock .python-version ./

# Copy application source and default config
COPY docsplitter/ ./docsplitter/
COPY config/default.yaml ./config/default.yaml

# Install only the project package itself (deps are already in .venv)
RUN uv pip install --python .venv/bin/python --no-deps .

# Runtime directories (overridden by volume mounts in production)
RUN mkdir -p watch output data config

# Non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["docsplitter"]

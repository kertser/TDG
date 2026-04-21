################################################################################
# KShU / TDG — Backend Dockerfile
#
# Multi-stage build:
#   builder  — installs Python deps (including GDAL/rasterio)
#   runtime  — minimal production image
#
# Usage:
#   docker build -t tdg-backend .
#   docker run --env-file .env tdg-backend
################################################################################

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

# System libs needed by rasterio (GDAL) and psycopg2/asyncpg
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        gcc \
        g++ \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps into /app/.venv so we can copy them cleanly
COPY requirements.txt .

# Install Python packages (rasterio will find GDAL via system libs)
RUN pip install --upgrade pip && \
    pip install --prefix=/install -r requirements.txt

# ── Stage 2: Runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Runtime-only system libs
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgdal-dev \
        gdal-bin \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy application code
COPY backend/        ./backend/
COPY alembic/        ./alembic/
COPY alembic.ini     ./alembic.ini
COPY FIELD_MANUAL.md ./FIELD_MANUAL.md

# Copy entrypoint
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

# Non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]


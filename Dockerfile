FROM python:3.12-slim AS builder

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /uvx /bin/

WORKDIR /build

# Copy dependency specifications
COPY requirements.txt pyproject.toml ./

# Install Python dependencies into a virtual environment
RUN uv venv /opt/venv && \
    VIRTUAL_ENV=/opt/venv uv pip install --no-cache -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Create a non-root user for security
RUN groupadd --gid 1000 appuser && \
    useradd --uid 1000 --gid appuser --shell /bin/bash --create-home appuser

# Copy the virtual environment from builder with proper permissions
COPY --from=builder --chown=appuser:appuser /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
RUN chown -R appuser:appuser /app

# Copy application source
COPY --chown=appuser:appuser app/ ./app/

# Switch to non-root user
USER appuser

# Expose FastAPI port
EXPOSE 8000

# Health check using Python (no curl needed in slim image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')" || exit 1

# Run with 1 worker to keep memory usage low on 1GB RAM VPS
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--limit-concurrency", "50", "--timeout-keep-alive", "30"]

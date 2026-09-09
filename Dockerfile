FROM python:3.12-slim

# Install uv for fast dependency installation
COPY --from=ghcr.io/astral-sh/uv:0.9.8 /uv /uvx /bin/

WORKDIR /app

# Copy dependency specifications
COPY requirements.txt pyproject.toml ./

# Install Python dependencies into system python
RUN uv pip install --system --no-cache -r requirements.txt

# Copy application source
COPY app/ ./app/

# Expose FastAPI port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

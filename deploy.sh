#!/bin/bash

set -e  # Exit on error

# ==============================================================================
# Expense Manager Backend — Zero-Downtime Deployment Script
# Usage: ./deploy.sh [.env.production]
# ==============================================================================

APP_NAME="expense-manager-backend"
CONTAINER_NAME="expense-manager-backend-container"
PORT=8000
INTERNAL_PORT=8000
TEMP_NAME="${CONTAINER_NAME}-candidate"
TEMP_PORT=$((PORT + 10000))   # 18000 for health-check
HEALTH_RETRIES=15
HEALTH_DELAY=3
HEALTH_ENDPOINT="/api/v1/health"
MEMORY_LIMIT="768m"           # Cap memory — leave ~256m for OS + Nginx on 1GB VPS
CPU_LIMIT="1.5"               # Use 1.5 of 2 CPUs, leave headroom for OS + Nginx

echo "🚀 Starting zero-downtime deployment for ${APP_NAME}..."

# ─── Step 1: Pull latest code ────────────────────────────────────────────────
echo "📥 Pulling latest code..."
git pull

# ─── Step 2: Resolve environment file ────────────────────────────────────────
ENV_FILE="${1:-}"
if [ -n "$ENV_FILE" ] && [ -f "$ENV_FILE" ]; then
  echo "📋 Using specified environment file: $ENV_FILE"
elif [ -f .env.production ]; then
  ENV_FILE=".env.production"
elif [ -f .env ]; then
  ENV_FILE=".env"
else
  echo "❌ Error: No environment file found! Create .env.production from .env.production.example"
  exit 1
fi

echo "📋 Using environment file: $ENV_FILE"

# ─── Step 3: Build candidate Docker image (old container stays live) ─────────
echo "🏗️  Building candidate Docker image (live container still running)..."
docker build \
  -t "${APP_NAME}:candidate" \
  -f Dockerfile \
  .

# ─── Step 4: Run candidate container on temporary port for health check ──────
echo "🧪 Starting candidate container on temporary port ${TEMP_PORT}..."
docker rm -f "$TEMP_NAME" 2>/dev/null || true
docker run -d \
  --name "$TEMP_NAME" \
  -p "${TEMP_PORT}:${INTERNAL_PORT}" \
  --memory="${MEMORY_LIMIT}" \
  --cpus="${CPU_LIMIT}" \
  --env-file "$ENV_FILE" \
  -e ENVIRONMENT=production \
  "${APP_NAME}:candidate"

# ─── Step 5: Health check polling loop ───────────────────────────────────────
echo "🏥 Waiting for candidate container health check..."
HEALTHY=false
for i in $(seq 1 $HEALTH_RETRIES); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${TEMP_PORT}${HEALTH_ENDPOINT}" 2>/dev/null || true)
    [ -z "$HTTP_CODE" ] && HTTP_CODE="000"
    if [ "$HTTP_CODE" = "200" ]; then
        HEALTHY=true
        echo "✅ Candidate container healthy after ${i} check(s). (HTTP ${HTTP_CODE})"
        break
    fi
    echo "⏳ Not ready yet (attempt $i/$HEALTH_RETRIES, HTTP ${HTTP_CODE})..."
    sleep $HEALTH_DELAY
done

if [ "$HEALTHY" != "true" ]; then
    echo ""
    echo "❌ Candidate failed health check after ${HEALTH_RETRIES} attempts. Aborting."
    echo "📋 Candidate container state:"
    docker inspect -f '   Status: {{.State.Status}} | ExitCode: {{.State.ExitCode}} | OOMKilled: {{.State.OOMKilled}} | Error: "{{.State.Error}}"' "$TEMP_NAME" 2>/dev/null || true
    echo "📋 Last 50 lines of candidate container logs:"
    docker logs --tail 50 "$TEMP_NAME" 2>&1 || true
    docker rm -f "$TEMP_NAME" >/dev/null 2>&1 || true
    exit 1
fi

# ─── Step 6: Swap live container ─────────────────────────────────────────────
echo "🔄 Candidate is healthy. Swapping live container..."

# Stop and remove candidate (it was only for health check)
docker rm -f "$TEMP_NAME" 2>/dev/null || true

# Tag the candidate as the live image
docker tag "${APP_NAME}:candidate" "${APP_NAME}:latest"

# Remove old live container
echo "🛑 Removing old container..."
docker rm -f "$CONTAINER_NAME" 2>/dev/null || true

# Start new live container
echo "▶️  Starting new container on port ${PORT}..."
docker run -d \
  --name "$CONTAINER_NAME" \
  -p "${PORT}:${INTERNAL_PORT}" \
  --memory="${MEMORY_LIMIT}" \
  --cpus="${CPU_LIMIT}" \
  --env-file "$ENV_FILE" \
  -e ENVIRONMENT=production \
  --restart unless-stopped \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  "${APP_NAME}:latest"

# ─── Step 7: Cleanup old images ─────────────────────────────────────────────
echo "🧹 Cleaning up dangling images..."
docker image prune -f 2>/dev/null || true

echo ""
echo "=============================================="
echo "✅ Deployment completed successfully!"
echo "   Container: ${CONTAINER_NAME}"
echo "   Port:      ${PORT}"
echo "   Health:    http://localhost:${PORT}${HEALTH_ENDPOINT}"
echo "   Logs:      docker logs -f ${CONTAINER_NAME}"
echo "=============================================="

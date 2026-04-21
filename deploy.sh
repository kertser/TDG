#!/usr/bin/env bash
################################################################################
# KShU / TDG — Docker Deployment Script (Linux/Unix)
#
# Builds and starts the full stack:
#   - PostgreSQL + PostGIS
#   - Redis
#   - FastAPI backend (with migrations)
#   - Nginx (reverse proxy + static frontend)
#   - Local LLM (llama.cpp) — always started by default
#
# The local LLM container is always included.
# To disable it, remove COMPOSE_PROFILES=llm from your .env.
#
# Usage:
#   ./deploy.sh              # Deploy full stack (with LLM)
#   ./deploy.sh --llm        # (no-op, kept for backward compat)
#   ./deploy.sh --rebuild    # Force rebuild images
#   ./deploy.sh --clean      # Full cleanup then rebuild
#   ./deploy.sh --down       # Stop & remove everything
#   ./deploy.sh --logs       # Start and follow logs
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
GRAY='\033[0;37m'
NC='\033[0m' # No Color

# Parse arguments
REBUILD=false
CLEAN=false
DOWN=false
LOGS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --llm)     shift ;;   # no-op: LLM always starts via COMPOSE_PROFILES=llm in .env
        --rebuild) REBUILD=true; shift ;;
        --clean)   CLEAN=true;   shift ;;
        --down)    DOWN=true;    shift ;;
        --logs)    LOGS=true;    shift ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${CYAN}  KShU / TDG — Docker Deployment Script${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""

# ── Helper: bring down all profiles ──────────────────────────────────────────
do_down() {
    local WITH_VOLUMES=$1
    echo -e "${YELLOW}🛑 Stopping all containers (all profiles)...${NC}"
    if [ "$WITH_VOLUMES" = "true" ]; then
        docker compose --profile llm down -v --remove-orphans
    else
        docker compose --profile llm down --remove-orphans
    fi
}

# ── --down: stop containers, keep volumes (data preserved) ────────────────────
if [ "$DOWN" = true ]; then
    do_down false
    echo -e "${GREEN}✅ All containers stopped. Database volume preserved.${NC}"
    echo -e "${GRAY}   To also wipe data: ./deploy.sh --clean${NC}"
    exit 0
fi

# ── --clean: full wipe → rebuild ──────────────────────────────────────────────
if [ "$CLEAN" = true ]; then
    echo -e "${YELLOW}🧹 Full cleanup: removing containers, volumes, images and build cache...${NC}"

    do_down true

    for img in tdg-backend tdg_backend; do
        ID=$(docker images -q "$img" 2>/dev/null || true)
        if [ -n "$ID" ]; then
            echo -e "${GRAY}   Removing image: $img ($ID)${NC}"
            docker rmi -f "$ID"
        fi
    done

    echo -e "${GRAY}   Pruning Docker build cache...${NC}"
    docker builder prune -f

    echo -e "${GREEN}✅ Cleanup complete. Proceeding with fresh build...${NC}"
    echo ""

    REBUILD=true
fi

# ── Check .env file ──────────────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo -e "${YELLOW}⚠️  .env file not found. Creating from .env.example...${NC}"
    if [ -f .env.example ]; then
        cp .env.example .env
        echo -e "${GREEN}✅ Created .env — please edit it with your actual keys before deploying.${NC}"
        echo ""
        echo -e "${CYAN}   Required settings:${NC}"
        echo -e "     - OPENAI_API_KEY (for LLM order parsing)"
        echo -e "     - SECRET_KEY (for JWT tokens)"
        echo -e "     - ADMIN_PASSWORD (for admin panel)"
        echo ""
        exit 1
    else
        echo -e "${RED}❌ .env.example not found! Cannot create .env.${NC}"
        exit 1
    fi
fi

# ── Build images ─────────────────────────────────────────────────────────────
if [ "$REBUILD" = true ]; then
    echo -e "${YELLOW}🛑 Bringing down existing stack before rebuild...${NC}"
    docker compose --profile llm down --remove-orphans
    echo ""
    echo -e "${YELLOW}🔨 Rebuilding Docker images (no cache)...${NC}"
    docker compose build --no-cache
else
    echo -e "${YELLOW}🔨 Building Docker images (using cache)...${NC}"
    docker compose build
fi

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Docker build failed.${NC}"
    exit 1
fi

# ── Start services (LLM always included via COMPOSE_PROFILES=llm in .env) ────
echo ""
echo -e "${GREEN}🚀 Starting services (including local LLM)...${NC}"
docker compose up -d

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Docker compose up failed.${NC}"
    exit 1
fi

# ── Wait for services to be healthy ──────────────────────────────────────────
echo ""
echo -e "${YELLOW}⏳ Waiting for services to become healthy...${NC}"

MAX_WAIT=180
WAITED=0
INTERVAL=5

while [ $WAITED -lt $MAX_WAIT ]; do
    BACKEND_HEALTH=$(docker inspect tdg-backend --format='{{.State.Health.Status}}' 2>/dev/null || echo "starting")
    NGINX_RUNNING=$(docker inspect tdg-nginx --format='{{.State.Running}}' 2>/dev/null || echo "false")

    if [ "$BACKEND_HEALTH" = "healthy" ] && [ "$NGINX_RUNNING" = "true" ]; then
        echo -e "${GREEN}✅ All services are up and healthy!${NC}"
        break
    fi

    echo -e "${GRAY}   Backend: $BACKEND_HEALTH | Nginx: $NGINX_RUNNING | Waited: ${WAITED}s / ${MAX_WAIT}s${NC}"
    sleep $INTERVAL
    WAITED=$((WAITED + INTERVAL))
done

if [ $WAITED -ge $MAX_WAIT ]; then
    echo ""
    echo -e "${YELLOW}⚠️  Services did not become healthy within ${MAX_WAIT}s.${NC}"
    echo -e "${GRAY}   Check logs with: docker compose logs -f backend${NC}"
    echo ""
    docker logs tdg-backend --tail 40
fi

# ── Print access info ────────────────────────────────────────────────────────
echo ""
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  🎯 KShU is running!${NC}"
echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "  Frontend:     ${YELLOW}http://localhost${NC}"
echo -e "  Backend API:  ${YELLOW}http://localhost/api${NC}"
echo -e "  WebSocket:    ${YELLOW}ws://localhost/ws${NC}"
echo ""
echo -e "  Database:     ${GRAY}localhost:5432 (tdg / tdg_secret)${NC}"
echo -e "  Redis:        ${GRAY}localhost:6379${NC}"
echo -e "  Local LLM:    ${YELLOW}http://localhost:8081${NC} ${GRAY}(OpenAI-compatible)${NC}"
echo ""
echo -e "  To view logs:    ${CYAN}docker compose logs -f${NC}"
echo -e "  To stop:         ${CYAN}./deploy.sh --down${NC}"
echo -e "  To rebuild:      ${CYAN}./deploy.sh --rebuild${NC}"
echo -e "  Full clean:      ${CYAN}./deploy.sh --clean${NC}"
echo ""

# ── Follow logs if requested ─────────────────────────────────────────────────
if [ "$LOGS" = true ]; then
    echo -e "${YELLOW}📋 Following logs (Ctrl+C to exit)...${NC}"
    echo ""
    docker compose logs -f
fi


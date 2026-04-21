#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy KShU / TDG to Docker with a single command.

.DESCRIPTION
    Builds and starts the full stack:
      - PostgreSQL + PostGIS
      - Redis
      - FastAPI backend (with migrations)
      - Nginx (reverse proxy + static frontend)
      - Local LLM (llama.cpp) — always started by default

    The local LLM container is always included.
    To disable it, remove COMPOSE_PROFILES=llm from your .env.

    Optional:
      --llm       (no-op, kept for backward compatibility — LLM always starts)
      --hot       Quick update: rebuild + restart backend only (for .py changes)
                  JS/HTML/CSS changes need nothing — nginx serves them directly from disk
      --rebuild   Force no-cache rebuild of images (brings stack down first)
      --clean     Full cleanup: stop containers, remove volumes, remove images,
                  purge build cache — then rebuild and start fresh
      --down      Stop and remove all containers and volumes (then exit)
      --logs      Follow logs after start

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 --hot       # Update Python files only (~10-30s)
    .\deploy.ps1 --rebuild
    .\deploy.ps1 --clean
    .\deploy.ps1 --down
#>

param(
    [switch]$llm,       # no-op: LLM always starts via COMPOSE_PROFILES=llm in .env
    [switch]$rebuild,
    [switch]$clean,
    [switch]$down,
    [switch]$logs,
    [switch]$hot        # Quick update: rebuild + restart backend only (JS/HTML updates need nothing)
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  KShU / TDG — Docker Deployment Script" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Helper: bring down all profiles ──────────────────────────────────────────
function Invoke-Down {
    param([switch]$WithVolumes)
    Write-Host "🛑 Stopping all containers (all profiles)..." -ForegroundColor Yellow
    if ($WithVolumes) {
        docker compose --profile llm down -v --remove-orphans
    } else {
        docker compose --profile llm down --remove-orphans
    }
}

# ── --hot: rebuild + restart backend only (fast Python update) ────────────────
if ($hot) {
    Write-Host "⚡ Hot update: rebuilding backend only..." -ForegroundColor Cyan
    Write-Host "   (JS/HTML changes are live instantly — no action needed for frontend)" -ForegroundColor Gray
    Write-Host ""
    docker compose build backend
    if ($LASTEXITCODE -ne 0) { Write-Host "❌ Build failed." -ForegroundColor Red; exit $LASTEXITCODE }
    docker compose up -d --no-deps backend
    if ($LASTEXITCODE -ne 0) { Write-Host "❌ Restart failed." -ForegroundColor Red; exit $LASTEXITCODE }
    Write-Host ""
    Write-Host "✅ Backend updated and restarted." -ForegroundColor Green
    Write-Host "   Logs: docker compose logs -f backend" -ForegroundColor Gray
    exit 0
}

# ── --down: stop containers, keep volumes (data preserved) ────────────────────
if ($down) {
    Invoke-Down
    Write-Host "✅ All containers stopped. Database volume preserved." -ForegroundColor Green
    Write-Host "   To also wipe data: .\deploy.ps1 --clean" -ForegroundColor Gray
    exit 0
}

# ── --clean: full wipe → rebuild ──────────────────────────────────────────────
if ($clean) {
    Write-Host "🧹 Full cleanup: removing containers, volumes, images and build cache..." -ForegroundColor Yellow

    Invoke-Down -WithVolumes

    $images = @("tdg-backend", "tdg_backend")
    foreach ($img in $images) {
        $id = docker images -q $img 2>$null
        if ($id) {
            Write-Host "   Removing image: $img ($id)" -ForegroundColor Gray
            docker rmi -f $id
        }
    }

    Write-Host "   Pruning Docker build cache..." -ForegroundColor Gray
    docker builder prune -f

    Write-Host "✅ Cleanup complete. Proceeding with fresh build..." -ForegroundColor Green
    Write-Host ""

    $rebuild = $true
}

# ── Check .env file ──────────────────────────────────────────────────────────
if (-not (Test-Path ".env")) {
    Write-Host "⚠️  .env file not found. Creating from .env.example..." -ForegroundColor Yellow
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "✅ Created .env — please edit it with your actual keys before deploying." -ForegroundColor Green
        Write-Host ""
        Write-Host "   Required settings:" -ForegroundColor Cyan
        Write-Host "     - OPENAI_API_KEY (for LLM order parsing)" -ForegroundColor Gray
        Write-Host "     - SECRET_KEY (for JWT tokens)" -ForegroundColor Gray
        Write-Host "     - ADMIN_PASSWORD (for admin panel)" -ForegroundColor Gray
        Write-Host ""
        exit 1
    } else {
        Write-Host "❌ .env.example not found! Cannot create .env." -ForegroundColor Red
        exit 1
    }
}

# ── Build images ─────────────────────────────────────────────────────────────
if ($rebuild) {
    Write-Host "🛑 Bringing down existing stack before rebuild..." -ForegroundColor Yellow
    docker compose --profile llm down --remove-orphans
    Write-Host ""
    Write-Host "🔨 Rebuilding Docker images (no cache)..." -ForegroundColor Yellow
    docker compose build --no-cache
} else {
    Write-Host "🔨 Building Docker images (using cache)..." -ForegroundColor Yellow
    docker compose build
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker build failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

# ── Start services (LLM always included via COMPOSE_PROFILES=llm in .env) ────
Write-Host ""
Write-Host "🚀 Starting services (including local LLM)..." -ForegroundColor Green
docker compose up -d

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Docker compose up failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

# ── Wait for services to be healthy ──────────────────────────────────────────
Write-Host ""
Write-Host "⏳ Waiting for services to become healthy..." -ForegroundColor Yellow

$maxWait = 180
$waited  = 0
$interval = 5

while ($waited -lt $maxWait) {
    $backendHealthy = docker inspect tdg-backend --format='{{.State.Health.Status}}' 2>$null
    $nginxRunning   = docker inspect tdg-nginx   --format='{{.State.Running}}'       2>$null

    if ($backendHealthy -eq "healthy" -and $nginxRunning -eq "true") {
        Write-Host "✅ All services are up and healthy!" -ForegroundColor Green
        break
    }

    Write-Host "   Backend: $backendHealthy | Nginx: $nginxRunning | Waited: ${waited}s / ${maxWait}s" -ForegroundColor Gray
    Start-Sleep -Seconds $interval
    $waited += $interval
}

if ($waited -ge $maxWait) {
    Write-Host ""
    Write-Host "⚠️  Services did not become healthy within ${maxWait}s." -ForegroundColor Yellow
    Write-Host "   Check logs with: docker compose logs -f backend" -ForegroundColor Gray
    Write-Host ""
    docker logs tdg-backend --tail 40
}

# ── Print access info ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  🎯 KShU is running!" -ForegroundColor Green
Write-Host "═══════════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Frontend:     " -NoNewline; Write-Host "http://localhost"       -ForegroundColor Yellow
Write-Host "  Backend API:  " -NoNewline; Write-Host "http://localhost/api"   -ForegroundColor Yellow
Write-Host "  WebSocket:    " -NoNewline; Write-Host "ws://localhost/ws"      -ForegroundColor Yellow
Write-Host ""
Write-Host "  Database:     " -NoNewline; Write-Host "localhost:5432 (tdg / tdg_secret)" -ForegroundColor Gray
Write-Host "  Redis:        " -NoNewline; Write-Host "localhost:6379"         -ForegroundColor Gray
Write-Host "  Local LLM:    " -NoNewline; Write-Host "http://localhost:8081 (OpenAI-compatible)" -ForegroundColor Gray
Write-Host ""
  Write-Host "  To view logs:    " -NoNewline; Write-Host "docker compose logs -f"   -ForegroundColor Cyan
  Write-Host "  To stop:         " -NoNewline; Write-Host ".\deploy.ps1 --down"      -ForegroundColor Cyan
  Write-Host "  To rebuild:      " -NoNewline; Write-Host ".\deploy.ps1 --rebuild"   -ForegroundColor Cyan
  Write-Host "  Hot update (.py):" -NoNewline; Write-Host ".\deploy.ps1 --hot"       -ForegroundColor Cyan
  Write-Host "  Full clean:      " -NoNewline; Write-Host ".\deploy.ps1 --clean"     -ForegroundColor Cyan
Write-Host ""

# ── Follow logs if requested ─────────────────────────────────────────────────
if ($logs) {
    Write-Host "📋 Following logs (Ctrl+C to exit)..." -ForegroundColor Yellow
    Write-Host ""
    docker compose logs -f
}

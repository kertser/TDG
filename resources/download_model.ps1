# Download a GGUF model for the local LLM sidecar.
#
# Usage:
#   .\scripts\download_model.ps1                                # default model
#   .\scripts\download_model.ps1 -Url <url> -FileName <name>   # custom model
#
# Recommended models (pick ONE — larger = better quality, slower):
#
#  Model                          Size    Speed   Quality  Context
#  ─────────────────────────────  ──────  ──────  ───────  ───────
#  Llama-3.2-1B-Instruct-Q4_K_S  ~700MB  fast    basic    8K
#  Llama-3.2-3B-Instruct-Q4_K_M  ~1.8GB  medium  good     8K
#  Phi-3.5-mini-Q4_K_M           ~2.2GB  medium  good     128K
#  Mistral-7B-Instruct-Q4_K_M    ~4.4GB  slow    great    32K
#
# For structured JSON output (scoring, profiles), 3B+ is recommended.

param(
    [string]$Url = "https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    [string]$FileName = "model.gguf"
)

$ErrorActionPreference = "Stop"
$ModelsDir = Join-Path (Split-Path $PSScriptRoot) "models"
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null

$Dest = Join-Path $ModelsDir $FileName

if (Test-Path $Dest) {
    $size = (Get-Item $Dest).Length / 1MB
    Write-Host "✓ Model already exists: $Dest ($([math]::Round($size))MB)" -ForegroundColor Green
    Write-Host "  Delete it first if you want to re-download."
    exit 0
}

Write-Host "Downloading model to $Dest ..."
Write-Host "  URL: $Url"
Write-Host ""

$ProgressPreference = "SilentlyContinue"  # speeds up Invoke-WebRequest significantly
Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing

$size = (Get-Item $Dest).Length / 1MB
Write-Host ""
Write-Host "✓ Model downloaded: $Dest ($([math]::Round($size))MB)" -ForegroundColor Green
Write-Host "  Start the LLM sidecar with: docker compose up llm"


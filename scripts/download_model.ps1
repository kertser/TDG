# Download a GGUF model for the local LLM sidecar.
#
# Usage:
#   .\scripts\download_model.ps1                                # default model (1B, fast)
#   .\scripts\download_model.ps1 -Size "3B"                     # 3B model (recommended for JSON)
#   .\scripts\download_model.ps1 -Url <url> -FileName <name>    # custom model
#
# Recommended models (pick ONE — larger = better quality, slower):
#
#  Model                          Size    Speed   Quality  Context  JSON Output
#  ─────────────────────────────  ──────  ──────  ───────  ───────  ───────────
#  Llama-3.2-1B-Instruct-Q4_K_S  ~700MB  fast    basic    8K       weak
#  Llama-3.2-3B-Instruct-Q4_K_M  ~1.8GB  medium  good     8K       decent
#  Phi-3.5-mini-Q4_K_M           ~2.2GB  medium  good     128K     good
#  Mistral-7B-Instruct-Q4_K_M    ~4.4GB  slow    great    32K     great
#
# For structured JSON output (order parsing, Red AI), 3B+ is recommended.

param(
    [ValidateSet("1B", "3B", "phi", "7B", "custom")]
    [string]$Size = "",

    [string]$Url = "",
    [string]$FileName = "model.gguf"
)

$ErrorActionPreference = "Stop"

# Pre-defined model URLs
$Models = @{
    "1B"  = "https://huggingface.co/unsloth/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_S.gguf"
    "3B"  = "https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf"
    "phi" = "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
    "7B"  = "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q4_K_M.gguf"
}

# Resolve URL
if (-not $Url) {
    if (-not $Size) {
        $Size = "1B"
        Write-Host "No size specified, defaulting to 1B (Llama-3.2-1B-Instruct-Q4_K_S)" -ForegroundColor Yellow
    }
    $Url = $Models[$Size]
    Write-Host "Selected model: $Size" -ForegroundColor Cyan
}

$ModelsDir = Join-Path (Split-Path $PSScriptRoot) "models"
New-Item -ItemType Directory -Force -Path $ModelsDir | Out-Null

$Dest = Join-Path $ModelsDir $FileName

if (Test-Path $Dest) {
    $size = (Get-Item $Dest).Length / 1MB
    Write-Host "`nModel already exists: $Dest ($([math]::Round($size))MB)" -ForegroundColor Green
    Write-Host "  Delete it first if you want to re-download."
    exit 0
}

Write-Host "`nDownloading model to $Dest ..." -ForegroundColor Cyan
Write-Host "  URL: $Url"
Write-Host ""

$ProgressPreference = "SilentlyContinue"  # speeds up Invoke-WebRequest significantly
Invoke-WebRequest -Uri $Url -OutFile $Dest -UseBasicParsing

$size = (Get-Item $Dest).Length / 1MB
Write-Host ""
Write-Host "Model downloaded: $Dest ($([math]::Round($size))MB)" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Start the LLM sidecar:  docker compose --profile llm up -d"
Write-Host "  2. Test it:                 python scripts\test_local_llm.py"
Write-Host "  3. To use WITHOUT OpenAI:   clear OPENAI_API_KEY in .env"


# Download a GGUF model for the local LLM sidecar.
#
# Usage:
#   .\scripts\download_model.ps1                                # default model (1B, fast)
#   .\scripts\download_model.ps1 -Size "3B"                     # 3B model (recommended for JSON)
#   .\scripts\download_model.ps1 -Url <url> -FileName <name>    # custom model
#
# Recommended models (pick ONE — larger = better quality, slower):
#
#  Model                          Size    Speed*  Quality  Context  JSON Output
#  ─────────────────────────────  ──────  ──────  ───────  ───────  ───────────
#  Gemma-3-1B-it-Q4_K_M ★DEFAULT ~0.8GB  ~15t/s  good     32K      good
#  Llama-3.2-1B-Q4_K_M            ~0.8GB  ~15t/s  good     128K     good
#  Phi-3.5-mini-Q4_K_M            ~2.2GB  ~8t/s   good     128K     excellent
#  Qwen2.5-3B-Instruct-Q4_K_M    ~2.0GB  ~6t/s   medium   32K      poor
#  Mistral-7B-Instruct-Q4_K_M    ~4.4GB  ~3t/s   great    32K      great
#
#  * Speed estimates for Linux server with AVX2. Windows/WSL2 may be 3-5× slower.

param(
    [ValidateSet("gemma1b", "llama1b", "phi", "qwen3b", "7B", "custom")]
    [string]$Size = "",

    [string]$Url = "",
    [string]$FileName = "model.gguf"
)

$ErrorActionPreference = "Stop"

# Pre-defined model URLs
$Models = @{
    "gemma1b"  = "https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf"
    "llama1b"  = "https://huggingface.co/bartowski/Llama-3.2-1B-Instruct-GGUF/resolve/main/Llama-3.2-1B-Instruct-Q4_K_M.gguf"
    "phi"      = "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
    "qwen3b"   = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
    "7B"       = "https://huggingface.co/TheBloke/Mistral-7B-Instruct-v0.2-GGUF/resolve/main/mistral-7b-instruct-v0.2.Q4_K_M.gguf"
}

# Resolve URL
if (-not $Url) {
    if (-not $Size) {
        $Size = "gemma1b"
        Write-Host "No size specified, defaulting to Gemma 3 1B Instruct Q4_K_M (small, fast)" -ForegroundColor Yellow
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


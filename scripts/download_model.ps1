# Download a GGUF model for the local LLM sidecar.
#
# Usage:
#   .\scripts\download_model.ps1                                # default model (best weak-CPU default)
#   .\scripts\download_model.ps1 -Size "3B"                     # 3B model (recommended for JSON)
#   .\scripts\download_model.ps1 -Url <url> -FileName <name>    # custom model
#
# Recommended models (pick ONE — larger = better quality, slower):
#
#  Model                          Size    Speed*  Quality  Context  JSON Output
#  ─────────────────────────────  ──────  ──────  ───────  ───────  ───────────
#  Gemma-3-1B-it-Q4_K_M ★DEFAULT ~0.8GB  fastest on weak CPU, strong JSON on this host
#  Qwen2.5-1.5B-Instruct-Q4_K_M  ~1.0GB  good bilingual fallback candidate
#  Llama-3.2-1B-Q4_K_M            ~0.8GB  fast, but weaker parser discipline than Gemma here
#  Phi-3.5-mini-Q4_K_M            ~2.2GB  slower, best quality if latency is acceptable
#  Qwen2.5-3B-Instruct-Q4_K_M     ~2.0GB  test only on stronger CPU
#
#  * Speed estimates for Linux server with AVX2. Windows/WSL2 may be 3-5× slower.

param(
    [ValidateSet("gemma1b", "qwen1.5b", "llama1b", "phi", "qwen3b", "7B", "custom")]
    [string]$Size = "",

    [string]$Url = "",
    [string]$FileName = "model.gguf"
)

$ErrorActionPreference = "Stop"

# Pre-defined model URLs
$Models = @{
    "gemma1b"  = "https://huggingface.co/bartowski/google_gemma-3-1b-it-GGUF/resolve/main/google_gemma-3-1b-it-Q4_K_M.gguf"
    "qwen1.5b" = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
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
Write-Host "  2. Warm prompt cache:       .\venv\Scripts\python.exe scripts\warm_local_llm.py"
Write-Host "  3. Benchmark real parser:   .\venv\Scripts\python.exe scripts\benchmark_order_parser.py"
Write-Host "  4. To use WITHOUT OpenAI:   clear OPENAI_API_KEY in .env"


<#
.SYNOPSIS
  Downloads llama.cpp Windows release, runs the server natively, and benchmarks models.

.DESCRIPTION
  Docker/WSL2 uses Sandy Bridge CPU backend (SSE4 only) → 0.6 tok/s.
  Native Windows llama.cpp uses AVX2/AVX-512 → 15-40 tok/s.

.EXAMPLE
  .\scripts\benchmark_native.ps1
#>

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$ROOT = Split-Path $PSScriptRoot
$LLAMACPP_DIR = Join-Path $ROOT "tools\llama-cpp"
$MODELS_DIR = Join-Path $ROOT "models"
$SERVER_EXE = Join-Path $LLAMACPP_DIR "llama-server.exe"
$MODEL_FILE = Join-Path $MODELS_DIR "model.gguf"
$RESULTS_FILE = Join-Path $MODELS_DIR "benchmark_results.txt"
$PORT = 8081

# ── Models to test ──
$Models = [ordered]@{
    "qwen1.5b" = @{
        Name = "Qwen2.5-1.5B-Instruct Q4_K_M"
        Url  = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf"
        Ctx  = 4096
    }
    "qwen3b" = @{
        Name = "Qwen2.5-3B-Instruct Q4_K_M"
        Url  = "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf"
        Ctx  = 4096
    }
    "phi3.5" = @{
        Name = "Phi-3.5-mini-instruct Q4_K_M"
        Url  = "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf"
        Ctx  = 4096
    }
}

# ── Step 1: Download llama.cpp Windows build ──
function Install-LlamaCpp {
    if (Test-Path $SERVER_EXE) {
        Write-Host "llama-server.exe already exists at $SERVER_EXE" -ForegroundColor Green
        return
    }

    Write-Host "`nDownloading llama.cpp Windows release..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Force -Path $LLAMACPP_DIR | Out-Null

    # Get latest release from GitHub
    $releaseUrl = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
    $release = Invoke-RestMethod -Uri $releaseUrl -Headers @{"User-Agent"="benchmark"}

    # Find the Windows CPU x64 asset (includes AVX2 support natively)
    $asset = $release.assets | Where-Object { $_.name -match "bin-win-cpu-x64" -and $_.name -match "\.zip$" } | Select-Object -First 1
    if (-not $asset) {
        # Fallback: any windows x64 zip that's NOT cuda/hip/sycl/opencl/cudart
        $asset = $release.assets | Where-Object {
            $_.name -match "win.*x64" -and $_.name -match "\.zip$" -and
            $_.name -notmatch "cuda|hip|sycl|opencl|cudart|vulkan|adreno"
        } | Select-Object -First 1
    }
    if (-not $asset) {
        Write-Host "ERROR: Could not find Windows release asset" -ForegroundColor Red
        Write-Host "Available assets:" -ForegroundColor Yellow
        $release.assets | ForEach-Object { Write-Host "  $($_.name)" }
        exit 1
    }

    Write-Host "  Asset: $($asset.name)" -ForegroundColor Cyan
    $zipPath = Join-Path $LLAMACPP_DIR "llama-cpp.zip"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath -UseBasicParsing

    Write-Host "  Extracting..." -ForegroundColor Cyan
    Expand-Archive -Path $zipPath -DestinationPath $LLAMACPP_DIR -Force
    Remove-Item $zipPath -Force

    # Find llama-server.exe (might be in a subfolder)
    $exe = Get-ChildItem -Path $LLAMACPP_DIR -Filter "llama-server.exe" -Recurse | Select-Object -First 1
    if (-not $exe) {
        Write-Host "ERROR: llama-server.exe not found after extraction" -ForegroundColor Red
        Get-ChildItem -Path $LLAMACPP_DIR -Recurse | Select-Object FullName | Format-List
        exit 1
    }

    # Move to expected location if in subfolder
    if ($exe.FullName -ne $SERVER_EXE) {
        $srcDir = $exe.DirectoryName
        Get-ChildItem -Path $srcDir -File | Move-Item -Destination $LLAMACPP_DIR -Force
        # Clean up empty dirs
        Get-ChildItem -Path $LLAMACPP_DIR -Directory | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host "  Installed: $SERVER_EXE" -ForegroundColor Green
}

# ── Step 2: Download a model ──
function Download-Model {
    param([string]$Url)

    if (Test-Path $MODEL_FILE) {
        Remove-Item $MODEL_FILE -Force
    }

    Write-Host "  Downloading model..." -ForegroundColor Cyan
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    Invoke-WebRequest -Uri $Url -OutFile $MODEL_FILE -UseBasicParsing
    $sw.Stop()

    $sizeMB = [math]::Round((Get-Item $MODEL_FILE).Length / 1MB)
    Write-Host "  Downloaded: ${sizeMB}MB in $([math]::Round($sw.Elapsed.TotalSeconds))s" -ForegroundColor Green
    return $sizeMB
}

# ── Step 3: Start server ──
function Start-LlamaServer {
    param([int]$CtxSize = 4096)

    # Kill any existing server
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 1

    Write-Host "  Starting llama-server (ctx=$CtxSize)..." -ForegroundColor Cyan
    $proc = Start-Process -FilePath $SERVER_EXE -ArgumentList @(
        "--model", $MODEL_FILE,
        "--alias", "local",
        "--host", "127.0.0.1",
        "--port", $PORT,
        "--ctx-size", $CtxSize,
        "--threads", (Get-CimInstance Win32_Processor).NumberOfLogicalProcessors,
        "--threads-batch", (Get-CimInstance Win32_Processor).NumberOfLogicalProcessors,
        "--parallel", "1",
        "--batch-size", "512",
        "--ubatch-size", "256",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "--mlock"
    ) -PassThru -WindowStyle Hidden -RedirectStandardError (Join-Path $LLAMACPP_DIR "server.log")

    # Wait for health
    $maxWait = 60
    for ($i = 0; $i -lt $maxWait; $i++) {
        Start-Sleep 2
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${PORT}/health" -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue
            if ($resp.StatusCode -eq 200) {
                Write-Host "  Server ready in $($i * 2)s" -ForegroundColor Green
                return $proc
            }
        } catch {}
    }

    Write-Host "  FAILED to start server!" -ForegroundColor Red
    if (Test-Path (Join-Path $LLAMACPP_DIR "server.log")) {
        Get-Content (Join-Path $LLAMACPP_DIR "server.log") | Select-Object -Last 10
    }
    return $null
}

# ── Step 4: Test a single message ──
function Test-Message {
    param(
        [string]$Message,
        [string]$SystemPrompt,
        [string]$ExpectedClass,
        [string]$ExpectedType
    )

    $body = @{
        model = "local"
        messages = @(
            @{ role = "system"; content = $SystemPrompt }
            @{ role = "user"; content = "MESSAGE: `"$Message`"`nPARSED:" }
        )
        temperature = 0.1
        max_tokens = 512
    } | ConvertTo-Json -Depth 5

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:${PORT}/v1/chat/completions" `
            -Method POST -Body $body -ContentType "application/json" `
            -TimeoutSec 180 -UseBasicParsing
        $sw.Stop()

        $data = $resp.Content | ConvertFrom-Json
        $content = $data.choices[0].message.content
        $promptTok = $data.usage.prompt_tokens
        $genTok = $data.usage.completion_tokens
        $elapsed = $sw.Elapsed.TotalSeconds
        $speed = if ($elapsed -gt 0) { [math]::Round($genTok / $elapsed, 1) } else { 0 }

        # Strip <think>...</think>
        $clean = $content -replace '(?s)<think>.*?</think>', '' | ForEach-Object { $_.Trim() }
        # Strip markdown fences
        if ($clean -match '^```') {
            $clean = ($clean -split "`n" | Select-Object -Skip 1) -join "`n"
            $clean = $clean -replace '```\s*$', '' | ForEach-Object { $_.Trim() }
        }

        $jsonValid = $false
        $gotClass = $null
        $gotType = $null
        try {
            $parsed = $clean | ConvertFrom-Json
            $jsonValid = $true
            $gotClass = $parsed.classification
            $gotType = $parsed.order_type
        } catch {}

        $classMatch = $gotClass -eq $ExpectedClass
        $status = if ($jsonValid -and $classMatch) { "OK" } else { "FAIL" }

        return @{
            Status = $status
            Time = [math]::Round($elapsed, 1)
            PromptTokens = $promptTok
            GenTokens = $genTok
            Speed = $speed
            JsonValid = $jsonValid
            GotClass = $gotClass
            GotType = $gotType
            ClassMatch = $classMatch
            Raw = $clean.Substring(0, [math]::Min(200, $clean.Length))
        }
    } catch {
        $sw.Stop()
        return @{
            Status = "ERROR"
            Time = [math]::Round($sw.Elapsed.TotalSeconds, 1)
            Error = $_.Exception.Message
        }
    }
}

# ── Main ──
Write-Host "TDG Local LLM Benchmark (Native Windows)" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

Install-LlamaCpp

$systemPrompt = @"
Parse radio messages into JSON. Respond with ONLY valid JSON, no extra text.
Units: A-squad, B-squad, Mortar Section, Recon Team.
Grid: alphanumeric (A-J, 1-10), snail subdivision 1-9. Example: B8-2-4.

Output JSON:
{"classification":"command|acknowledgment|status_request|status_report|unclear","language":"en|ru","target_unit_refs":["name"],"order_type":"move|attack|fire|defend|observe|disengage|halt|null","location_refs":[{"source_text":"text","ref_type":"snail|grid|coordinate","normalized":"B8-2-4"}],"speed":"slow|fast|null","confidence":0.9}
"@

$testMessages = @(
    @{ Msg = "A-squad, move to B8-2-4 fast!"; Class = "command"; Type = "move" }
    @{ Msg = "Mortar Section, fire at D7-8!"; Class = "command"; Type = "fire" }
    @{ Msg = "Roger, wilco."; Class = "acknowledgment"; Type = $null }
    @{ Msg = "B-squad, атакуйте в E5-3!"; Class = "command"; Type = "attack" }
)

$allResults = @()
"" | Set-Content $RESULTS_FILE

foreach ($modelKey in $Models.Keys) {
    $model = $Models[$modelKey]
    Write-Host "`n$('=' * 60)" -ForegroundColor Yellow
    Write-Host "  MODEL: $($model.Name)" -ForegroundColor Yellow
    Write-Host "$('=' * 60)" -ForegroundColor Yellow

    $sizeMB = Download-Model -Url $model.Url
    $proc = Start-LlamaServer -CtxSize $model.Ctx

    if (-not $proc) {
        Write-Host "  Skipping (server failed)" -ForegroundColor Red
        continue
    }

    $modelResults = @()
    foreach ($test in $testMessages) {
        Write-Host "`n  Message: `"$($test.Msg)`"" -ForegroundColor White
        $r = Test-Message -Message $test.Msg -SystemPrompt $systemPrompt `
            -ExpectedClass $test.Class -ExpectedType $test.Type

        if ($r.Status -eq "ERROR") {
            Write-Host "    ERROR ($($r.Time)s): $($r.Error)" -ForegroundColor Red
        } else {
            $color = if ($r.Status -eq "OK") { "Green" } else { "Red" }
            Write-Host "    [$($r.Status)] $($r.Time)s | $($r.PromptTokens)p+$($r.GenTokens)g | $($r.Speed) tok/s" -ForegroundColor $color
            Write-Host "    Class: $($r.GotClass) (exp: $($test.Class)) | Type: $($r.GotType) (exp: $($test.Type))"
            if (-not $r.JsonValid) {
                Write-Host "    JSON INVALID: $($r.Raw)" -ForegroundColor Red
            }
        }
        $modelResults += $r
    }

    # Summary for this model
    $okTests = $modelResults | Where-Object { $_.Status -ne "ERROR" }
    if ($okTests) {
        $avgTime = ($okTests | Measure-Object -Property Time -Average).Average
        $avgSpeed = ($okTests | Measure-Object -Property Speed -Average).Average
        $jsonPct = ($okTests | Where-Object { $_.JsonValid }).Count / $okTests.Count * 100
        $classPct = ($okTests | Where-Object { $_.ClassMatch }).Count / $okTests.Count * 100

        $summary = "  $($model.Name): Avg ${avgTime}s, ${avgSpeed} tok/s, JSON ${jsonPct}%, Class ${classPct}%"
        Write-Host "`n$summary" -ForegroundColor Cyan
        $summary | Add-Content $RESULTS_FILE

        $allResults += @{
            Model = $model.Name
            AvgTime = [math]::Round($avgTime, 1)
            AvgSpeed = [math]::Round($avgSpeed, 1)
            JsonPct = [math]::Round($jsonPct)
            ClassPct = [math]::Round($classPct)
            SizeMB = $sizeMB
        }
    }

    # Stop server and clean up
    Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 2
    if (Test-Path $MODEL_FILE) { Remove-Item $MODEL_FILE -Force }
}

# Final summary
Write-Host "`n$('=' * 70)" -ForegroundColor Cyan
Write-Host "  FINAL SUMMARY" -ForegroundColor Cyan
Write-Host "$('=' * 70)" -ForegroundColor Cyan
Write-Host ("{0,-40} {1,6} {2,8} {3,8} {4,6} {5,6}" -f "Model", "Size", "Time", "Tok/s", "JSON%", "Class%") -ForegroundColor White
Write-Host ("-" * 70)

foreach ($r in $allResults) {
    $color = if ($r.JsonPct -ge 75 -and $r.AvgSpeed -ge 5) { "Green" }
             elseif ($r.JsonPct -ge 50) { "Yellow" }
             else { "Red" }
    Write-Host ("{0,-40} {1,5}M {2,7}s {3,7} {4,5}% {5,5}%" -f $r.Model, $r.SizeMB, $r.AvgTime, $r.AvgSpeed, $r.JsonPct, $r.ClassPct) -ForegroundColor $color
}

Write-Host "`nResults saved to: $RESULTS_FILE" -ForegroundColor Cyan
Write-Host "Pick the model with best JSON% + Class% and acceptable speed." -ForegroundColor Yellow
Write-Host "Then update docker-compose.yml or run natively." -ForegroundColor Yellow



"""
Benchmark local LLM models for TDG order parsing.

Tests each candidate model by:
1. Downloading the GGUF file
2. Starting a llama.cpp Docker container
3. Sending a realistic order-parsing prompt (~16K tokens)
4. Measuring prompt processing time, generation speed, and JSON quality
5. Stopping container and deleting model

Usage:
    python -u scripts/benchmark_models.py
    python -u scripts/benchmark_models.py --models qwen3b phi3.5
    python -u scripts/benchmark_models.py --keep  # don't delete models after test
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import httpx
except ImportError:
    print("Installing httpx...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "httpx"])
    import httpx

# ── Candidate models ──────────────────────────────────────────────
MODELS = {
    "qwen3b": {
        "name": "Qwen2.5-3B-Instruct Q4_K_M",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "ctx": 32768,
        "size_mb": 2000,
    },
    "phi3.5": {
        "name": "Phi-3.5-mini-instruct Q4_K_M",
        "url": "https://huggingface.co/bartowski/Phi-3.5-mini-instruct-GGUF/resolve/main/Phi-3.5-mini-instruct-Q4_K_M.gguf",
        "ctx": 32768,  # use 32K even though it supports 128K (faster)
        "size_mb": 2200,
    },
    "gemma2b": {
        "name": "Gemma-2-2B-IT Q4_K_M",
        "url": "https://huggingface.co/bartowski/gemma-2-2b-it-GGUF/resolve/main/gemma-2-2b-it-Q4_K_M.gguf",
        "ctx": 8192,
        "size_mb": 1500,
    },
    "qwen1.5b": {
        "name": "Qwen2.5-1.5B-Instruct Q4_K_M",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "ctx": 32768,
        "size_mb": 1000,
    },
    "llama3b": {
        "name": "Llama-3.2-3B-Instruct Q4_K_M",
        "url": "https://huggingface.co/unsloth/Llama-3.2-3B-Instruct-GGUF/resolve/main/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "ctx": 8192,  # Llama-3.2 has 8K context (some claim 128K with RoPE but unreliable)
        "size_mb": 1800,
    },
}

# ── Realistic test prompt (simulates order parsing context) ────────
# This is a condensed version of what the order parser sends (~2K tokens
# for system + a medium-sized unit roster + game context).
# We also test with a padded version to simulate larger contexts.

SYSTEM_PROMPT_BASE = """You are a military radio communications parser for a tactical command exercise.
You receive radio messages and must classify and parse them into structured JSON.

Messages can be in English or Russian. Detect the language and parse accordingly.

## Classification
- command: actionable order (move, attack, fire, defend, observe, disengage, halt, resupply)
- acknowledgment: confirming receipt ("так точно", "roger", "wilco")
- status_request: asking for status ("доложите обстановку", "report status")
- status_report: reporting situation ("находимся в ...", "taking fire")
- unclear: cannot parse

## Order types
move, attack, fire, request_fire, defend, observe, support, withdraw, disengage, halt, regroup, resupply, breach, lay_mines, construct, deploy_bridge, split, merge, report_status

## Grid Reference Format
Grid squares: "B8", "C7". Snail subdivision 1-9 (spiral from top-left clockwise): "B8-2-4".
Coordinates: "48.85, 2.35" (lat, lon). Heights: "высота 170" / "height 170".

## Units in session
{unit_roster}

## Context: Grid Definition
Grid: 10 columns x 10 rows, 1000m squares. Labeling: alphanumeric (columns A-J, rows 1-10).
Snail subdivision: 3x3, max depth 3. Example: 'B4-3-7'.

## Context: Game Time
{game_time}

## Context: Terrain
{terrain_context}

## Context: Known Enemy Contacts
{contacts_context}

## Output JSON:
{{
  "classification": "command|acknowledgment|status_request|status_report|unclear",
  "language": "en|ru",
  "target_unit_refs": ["unit name"],
  "sender_ref": "sender or null",
  "order_type": "move|attack|fire|defend|observe|disengage|halt|resupply|null",
  "location_refs": [{{"source_text": "text", "ref_type": "grid|snail|coordinate|relative", "normalized": "B8-2-4"}}],
  "speed": "slow|fast|null",
  "formation": "column|line|wedge|null",
  "engagement_rules": "fire_at_will|hold_fire|return_fire_only|null",
  "urgency": "routine|priority|immediate|null",
  "confidence": 0.0-1.0
}}
"""

UNIT_ROSTER_SMALL = """- A-squad (infantry_platoon, blue, idle, 100%/100%/100%, pos: 49.0362,4.5110)
- B-squad (infantry_platoon, blue, idle, 85%/70%/90%, pos: 49.0380,4.5200)
- Mortar Section (mortar_section, blue, idle, 100%/80%/100%, pos: 49.0340,4.5050)
- Recon Team (recon_team, blue, idle, 100%/100%/100%, pos: 49.0410,4.5300)"""

# Pad with more units for larger context test
UNIT_ROSTER_LARGE = UNIT_ROSTER_SMALL + """
- C-squad (infantry_platoon, blue, moving, 90%/60%/85%, pos: 49.0400,4.5150)
- D-squad (infantry_squad, blue, idle, 100%/100%/95%, pos: 49.0350,4.5250)
- Tank Platoon (tank_platoon, blue, idle, 100%/90%/100%, pos: 49.0320,4.5000)
- Engineer Section (engineer_section, blue, idle, 100%/100%/100%, pos: 49.0370,4.5080)
- Logistics Unit (logistics_platoon, blue, idle, 100%/100%/100%, pos: 49.0300,4.4950)
- Sniper Team (sniper_team, blue, idle, 100%/100%/100%, pos: 49.0420,4.5350)
- Anti-Tank Team (at_team, blue, idle, 100%/85%/90%, pos: 49.0390,4.5120)
- HQ Section (hq_section, blue, idle, 100%/100%/100%, pos: 49.0310,4.4980)"""

TERRAIN_CONTEXT = """Terrain at unit positions:
- A-squad: open grassland, elevation 145m, slope 2°
- B-squad: forest edge, elevation 160m, slope 5°
- Mortar Section: scrub/bushes, elevation 138m, slope 1°
- Recon Team: hilltop (height 172), open, good observation
- C-squad: road intersection, urban fringe, elevation 150m
- D-squad: dense forest, elevation 155m, limited visibility
Nearby features: bridge at E6-2, minefield at F7-2 (discovered), roadblock at D5-4."""

CONTACTS_CONTEXT = """Known enemy contacts:
- Infantry platoon (~30 personnel) at E5-3, confidence 0.7, moving SE, last seen tick 12
- Armored vehicle at D7-8, confidence 0.5, stationary, last seen tick 10 (stale)
- Unknown unit at F6-1, confidence 0.3, last seen tick 8 (very stale)"""

# ── Test messages (mix of EN/RU, commands and non-commands) ────────
TEST_MESSAGES = [
    # Simple RU command
    ("A-squad, выдвигайтесь в B8-2-4, быстро!", "command", "move"),
    # EN command with engagement
    ("B-squad, advance to E5-3 and engage enemy forces. Move fast!", "command", "attack"),
    # RU acknowledgment
    ("Так точно, выполняем. Начали движение.", "acknowledgment", None),
    # EN status request
    ("Recon Team, report status and any contacts.", "status_request", "report_status"),
    # Complex RU command
    ("Миномётная секция, огонь по квадрату D7 улитка 8! Три залпа!", "command", "fire"),
]

CONTAINER_NAME = "tdg-llm-benchmark"
MODELS_DIR = Path(__file__).resolve().parent.parent / "models"
MODEL_FILE = MODELS_DIR / "model.gguf"
LLM_PORT = 8081
LLM_URL = f"http://localhost:{LLM_PORT}"


def log(msg: str, color: str = ""):
    colors = {"green": "\033[92m", "red": "\033[91m", "yellow": "\033[93m",
              "cyan": "\033[96m", "bold": "\033[1m", "": ""}
    reset = "\033[0m" if color else ""
    print(f"{colors.get(color, '')}{msg}{reset}", flush=True)


def run_cmd(cmd: list[str], check: bool = True, capture: bool = False, timeout: int = 300) -> subprocess.CompletedProcess:
    """Run a shell command."""
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, timeout=timeout)


def download_model(url: str, dest: Path) -> float:
    """Download model, return download time in seconds."""
    log(f"  Downloading from {url[:80]}...", "cyan")
    start = time.time()
    # Use httpx for streaming download with progress
    with httpx.stream("GET", url, follow_redirects=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=8 * 1024 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = downloaded / total * 100
                    print(f"\r  Progress: {pct:.0f}% ({downloaded / 1048576:.0f}/{total / 1048576:.0f} MB)", end="", flush=True)
    elapsed = time.time() - start
    print()
    size_mb = dest.stat().st_size / 1048576
    log(f"  Downloaded {size_mb:.0f} MB in {elapsed:.1f}s", "green")
    return elapsed


def stop_container():
    """Stop and remove the benchmark container if running."""
    run_cmd(["docker", "rm", "-f", CONTAINER_NAME], check=False, capture=True)
    time.sleep(1)


def start_container(ctx_size: int) -> float:
    """Start llama.cpp container, return startup time."""
    stop_container()
    log(f"  Starting llama.cpp container (ctx={ctx_size})...", "cyan")
    start = time.time()
    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-v", f"{MODELS_DIR}:/models:ro",
        "-p", f"{LLM_PORT}:8080",
        "--cap-add", "IPC_LOCK",
        "--shm-size", "2g",
        "-e", "OMP_NUM_THREADS=8",
        "ghcr.io/ggml-org/llama.cpp:server",
        "--model", "/models/model.gguf",
        "--alias", "local",
        "--host", "0.0.0.0",
        "--port", "8080",
        "--ctx-size", str(ctx_size),
        "--threads", "8",
        "--threads-batch", "8",
        "--parallel", "1",
        "--batch-size", "512",
        "--ubatch-size", "256",
        "--cache-type-k", "q4_0",
        "--cache-type-v", "q4_0",
        "--mlock",
    ]
    run_cmd(cmd)

    # Wait for health
    max_wait = 120
    for i in range(max_wait):
        time.sleep(2)
        try:
            resp = httpx.get(f"{LLM_URL}/health", timeout=3)
            if resp.status_code == 200:
                elapsed = time.time() - start
                log(f"  Server ready in {elapsed:.1f}s", "green")
                return elapsed
        except Exception:
            pass
        if i % 10 == 9:
            print(f"\r  Waiting for server... {i * 2}s", end="", flush=True)

    # Check container logs on failure
    result = run_cmd(["docker", "logs", "--tail", "20", CONTAINER_NAME], capture=True, check=False)
    log(f"\n  FAILED to start. Last logs:\n{result.stdout}\n{result.stderr}", "red")
    raise TimeoutError("Container did not become healthy")


def build_prompt(size: str = "small") -> tuple[str, str]:
    """Build system + user prompt. Returns (system, user_message)."""
    roster = UNIT_ROSTER_SMALL if size == "small" else UNIT_ROSTER_LARGE
    terrain = TERRAIN_CONTEXT if size == "large" else "Open terrain with scattered forests."
    contacts = CONTACTS_CONTEXT if size == "large" else "No known contacts."

    system = SYSTEM_PROMPT_BASE.format(
        unit_roster=roster,
        game_time="2026-04-17 08:30:00 (morning, clear weather, good visibility)",
        terrain_context=terrain,
        contacts_context=contacts,
    )
    return system, ""


def test_message(client: httpx.Client, system: str, message: str, timeout: float = 180) -> dict:
    """Send a test message and measure performance."""
    user_msg = f'MESSAGE: "{message}"\nPARSED:'

    start = time.time()
    try:
        resp = client.post(
            f"{LLM_URL}/v1/chat/completions",
            json={
                "model": "local",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.1,
                "max_tokens": 1024,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        elapsed = time.time() - start

        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}", "time_s": elapsed}

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Strip <think>...</think> blocks
        import re
        content_clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

        # Strip markdown fences
        if content_clean.startswith("```"):
            first_nl = content_clean.find("\n")
            if first_nl != -1:
                content_clean = content_clean[first_nl + 1:]
            if content_clean.endswith("```"):
                content_clean = content_clean[:-3].strip()

        # Try parsing JSON
        parsed = None
        json_valid = False
        classification = None
        order_type = None
        try:
            parsed = json.loads(content_clean)
            json_valid = True
            classification = parsed.get("classification")
            order_type = parsed.get("order_type")
        except json.JSONDecodeError:
            pass

        gen_speed = completion_tokens / elapsed if elapsed > 0 else 0

        return {
            "time_s": round(elapsed, 2),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "gen_speed_tps": round(gen_speed, 1),
            "json_valid": json_valid,
            "classification": classification,
            "order_type": order_type,
            "raw_content_preview": content[:200],
        }
    except httpx.TimeoutException:
        return {"error": "TIMEOUT", "time_s": time.time() - start}
    except Exception as e:
        return {"error": str(e), "time_s": time.time() - start}


def benchmark_model(model_key: str, model_info: dict, keep: bool = False) -> dict:
    """Run full benchmark for one model."""
    log(f"\n{'='*70}", "bold")
    log(f"  BENCHMARKING: {model_info['name']}", "bold")
    log(f"  URL: {model_info['url'][:80]}...", "cyan")
    log(f"  Expected size: ~{model_info['size_mb']} MB, Context: {model_info['ctx']}", "cyan")
    log(f"{'='*70}", "bold")

    results = {
        "model": model_info["name"],
        "model_key": model_key,
        "ctx_size": model_info["ctx"],
        "download_time_s": None,
        "startup_time_s": None,
        "tests": [],
        "error": None,
    }

    try:
        # Download
        if MODEL_FILE.exists():
            log("  Removing existing model.gguf...", "yellow")
            MODEL_FILE.unlink()

        dl_time = download_model(model_info["url"], MODEL_FILE)
        results["file_size_mb"] = round(MODEL_FILE.stat().st_size / 1048576)
        results["download_time_s"] = round(dl_time, 1)

        # Start container
        startup = start_container(model_info["ctx"])
        results["startup_time_s"] = round(startup, 1)

        # Build prompts
        system_small, _ = build_prompt("small")
        system_large, _ = build_prompt("large")

        with httpx.Client() as client:
            # Test each message with small context first
            log("\n  ── Small context tests ──", "cyan")
            for msg, expected_class, expected_type in TEST_MESSAGES:
                log(f"\n  Message: \"{msg[:60]}...\"")
                result = test_message(client, system_small, msg)

                if "error" in result:
                    log(f"    ERROR: {result['error']}", "red")
                else:
                    class_match = result["classification"] == expected_class
                    type_match = (expected_type is None) or (result["order_type"] == expected_type)
                    status = "✅" if (result["json_valid"] and class_match) else "❌"

                    log(f"    {status} Time: {result['time_s']}s, "
                        f"Tokens: {result['prompt_tokens']}→{result['completion_tokens']}, "
                        f"Speed: {result['gen_speed_tps']} tok/s",
                        "green" if class_match else "red")
                    log(f"    Classification: {result['classification']} (expected: {expected_class}), "
                        f"Order: {result['order_type']} (expected: {expected_type})")
                    if not result["json_valid"]:
                        log(f"    JSON INVALID! Raw: {result['raw_content_preview']}", "red")

                result["message"] = msg[:60]
                result["expected_class"] = expected_class
                result["expected_type"] = expected_type
                result["context_size"] = "small"
                results["tests"].append(result)

            # Test one message with large context
            log("\n  ── Large context test ──", "cyan")
            msg = TEST_MESSAGES[1][0]  # EN attack command
            log(f"\n  Message: \"{msg[:60]}...\" (large context)")
            result = test_message(client, system_large, msg, timeout=300)

            if "error" in result:
                log(f"    ERROR: {result['error']}", "red")
            else:
                log(f"    Time: {result['time_s']}s, "
                    f"Tokens: {result['prompt_tokens']}→{result['completion_tokens']}, "
                    f"Speed: {result['gen_speed_tps']} tok/s",
                    "green" if result["json_valid"] else "red")

            result["message"] = msg[:60]
            result["context_size"] = "large"
            results["tests"].append(result)

    except Exception as e:
        results["error"] = str(e)
        log(f"  BENCHMARK FAILED: {e}", "red")
    finally:
        stop_container()
        if not keep and MODEL_FILE.exists():
            log("  Cleaning up model file...", "yellow")
            MODEL_FILE.unlink()

    return results


def print_summary(all_results: list[dict]):
    """Print a comparison table."""
    log(f"\n{'='*90}", "bold")
    log("  BENCHMARK SUMMARY", "bold")
    log(f"{'='*90}", "bold")

    header = f"{'Model':<35} {'Size':>6} {'Startup':>8} {'Avg Time':>9} {'Avg TPS':>8} {'JSON%':>6} {'Class%':>7}"
    log(header, "cyan")
    log("-" * 90)

    for r in all_results:
        if r.get("error") and not r["tests"]:
            log(f"{r['model']:<35} FAILED: {r['error'][:40]}", "red")
            continue

        tests = [t for t in r["tests"] if "error" not in t]
        if not tests:
            log(f"{r['model']:<35} ALL TESTS FAILED", "red")
            continue

        avg_time = sum(t["time_s"] for t in tests) / len(tests)
        avg_tps = sum(t.get("gen_speed_tps", 0) for t in tests) / len(tests)
        json_pct = sum(1 for t in tests if t.get("json_valid")) / len(tests) * 100
        class_ok = sum(1 for t in tests
                       if t.get("classification") == t.get("expected_class")
                       and "expected_class" in t) / max(1, sum(1 for t in tests if "expected_class" in t)) * 100
        size_mb = r.get("file_size_mb", "?")
        startup = r.get("startup_time_s", "?")

        color = "green" if json_pct >= 80 and avg_tps >= 10 else "yellow" if json_pct >= 50 else "red"
        log(f"{r['model']:<35} {size_mb:>5}M {startup:>7}s {avg_time:>8.1f}s {avg_tps:>7.1f} {json_pct:>5.0f}% {class_ok:>6.0f}%", color)

    log(f"\n{'='*90}")
    log("  Recommendation: Pick the model with highest JSON% + Class% and acceptable speed.", "cyan")
    log("  Target: >80% JSON valid, >70% classification correct, <30s avg response time.", "cyan")


def main():
    parser = argparse.ArgumentParser(description="Benchmark local LLM models for TDG")
    parser.add_argument("--models", nargs="+", choices=list(MODELS.keys()),
                        default=list(MODELS.keys()),
                        help="Models to test (default: all)")
    parser.add_argument("--keep", action="store_true",
                        help="Keep model files after testing")
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    log("TDG Local LLM Benchmark", "bold")
    log(f"Testing {len(args.models)} models: {', '.join(args.models)}")
    log(f"Models directory: {MODELS_DIR}")

    # Ensure Docker is available
    try:
        run_cmd(["docker", "version"], capture=True)
    except Exception:
        log("ERROR: Docker not available. Please install/start Docker.", "red")
        sys.exit(1)

    # Pull llama.cpp image first
    log("\nPulling llama.cpp server image...", "cyan")
    run_cmd(["docker", "pull", "ghcr.io/ggml-org/llama.cpp:server"], check=False)

    all_results = []
    for model_key in args.models:
        result = benchmark_model(model_key, MODELS[model_key], keep=args.keep)
        all_results.append(result)

    print_summary(all_results)

    # Save raw results
    results_file = MODELS_DIR / "benchmark_results.json"
    with open(results_file, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    log(f"\nRaw results saved to: {results_file}", "cyan")


if __name__ == "__main__":
    main()


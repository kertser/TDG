"""Quick smoke-test for the local LLM sidecar integration.

Usage (after starting the sidecar):
    python scripts/test_local_llm.py

Checks:
1. Server is reachable at LOCAL_MODEL_URL
2. Chat completion works
3. JSON extraction works (order parsing format)
"""

from __future__ import annotations

import json
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

LOCAL_URL = os.environ.get("LOCAL_MODEL_URL", "http://localhost:8081/v1")
MODEL_NAME = os.environ.get("LOCAL_MODEL_NAME", "local")


def check_server():
    """Step 1: Check the server is up."""
    print(f"\n{'='*60}")
    print(f"1  Checking server at {LOCAL_URL}")
    print(f"{'='*60}")

    from openai import OpenAI

    client = OpenAI(base_url=LOCAL_URL, api_key="local")
    models = client.models.list()
    print(f"   OK  Server is up! Models: {[m.id for m in models.data]}")
    return client


def check_chat(client):
    """Step 2: Simple chat completion."""
    print(f"\n{'='*60}")
    print("2  Testing chat completion")
    print(f"{'='*60}")

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Reply in one short sentence."},
            {"role": "user", "content": "What is a tactical decision game?"},
        ],
        temperature=0.1,
        max_tokens=100,
    )
    text = resp.choices[0].message.content
    print(f"   Response: {text}")
    print(f"   Tokens: {resp.usage.completion_tokens} completion, {resp.usage.total_tokens} total")
    print("   OK  Chat completion works!")


def check_json_output(client):
    """Step 3: JSON extraction (what order parsing needs)."""
    print(f"\n{'='*60}")
    print("3  Testing JSON output (order parsing format)")
    print(f"{'='*60}")

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": (
                "You are a military radio message parser. Return ONLY a JSON object with these keys:\n"
                '{"classification": "command", "order_type": "move", "target_unit_refs": ["1st Platoon"], '
                '"location_refs": ["B4-3"], "speed": "fast", "confidence": 0.9}\n'
                "Return ONLY valid JSON. No markdown, no commentary."
            )},
            {"role": "user", "content": (
                "RADIO MESSAGE: 1st Platoon, advance rapidly to grid B4-3"
            )},
        ],
        temperature=0.1,
        max_tokens=300,
    )
    raw = resp.choices[0].message.content
    print(f"   Raw response:\n   {raw}")

    # Try to parse JSON from the response
    data = _extract_json(raw)
    if data:
        print(f"   Parsed: classification={data.get('classification')}, order_type={data.get('order_type')}")
        print("   OK  JSON extraction works!")
    else:
        print(f"   WARNING  JSON parse failed. This is expected with very small models (1B).")
        print("   For structured JSON, consider a 3B+ model.")


def _extract_json(raw: str):
    """Extract JSON from raw LLM output, handling markdown fences."""
    import re
    # Strip markdown code fences first
    cleaned = re.sub(r'```(?:json)?\s*', '', raw).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # Try to find the outermost { ... } block
    depth = 0
    start = None
    for i, c in enumerate(raw):
        if c == '{':
            if depth == 0:
                start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(raw[start:i+1])
                except json.JSONDecodeError:
                    start = None
    return None


def check_red_ai_format(client):
    """Step 4: Red AI decision format."""
    print(f"\n{'='*60}")
    print("4  Testing Red AI decision format")
    print(f"{'='*60}")

    resp = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": (
                "You are a Red Force military AI commander. Respond with ONLY a JSON object:\n"
                '{"orders": [{"unit_id": "unit-1", "order_type": "move", '
                '"target_location": "C5-2", "reasoning": "advancing to objective"}]}\n'
                "Return ONLY valid JSON."
            )},
            {"role": "user", "content": (
                "You command 1 infantry platoon at grid B3. "
                "Enemy spotted at D5. Your mission: advance and engage."
            )},
        ],
        temperature=0.3,
        max_tokens=300,
    )
    raw = resp.choices[0].message.content
    print(f"   Raw response:\n   {raw}")
    data = _extract_json(raw)
    if data:
        orders = data.get("orders", [])
        print(f"   Parsed: {len(orders)} order(s)")
        print("   OK  Red AI format works!")
    else:
        print("   WARNING  JSON parse failed — rule-based fallback will be used for Red AI.")


def main():
    print("===  Local LLM Integration Test for TDG  ===")
    print(f"   Server URL: {LOCAL_URL}")
    print(f"   Model name: {MODEL_NAME}")

    try:
        client = check_server()
    except Exception as e:
        print(f"\n   FAIL  Cannot reach server at {LOCAL_URL}")
        print(f"   Error: {e}")
        print("\n   Start the server first:")
        print("     docker compose --profile llm up -d")
        print("   Or run standalone:")
        print("     docker run --rm -v ./models:/models -p 8081:8000 tdg-llm")
        sys.exit(1)

    check_chat(client)
    check_json_output(client)
    check_red_ai_format(client)

    print(f"\n{'='*60}")
    print("  All checks complete!")
    print(f"{'='*60}")
    print("\nTo use local LLM in the game:")
    print("  1. Clear OPENAI_API_KEY in .env (or leave empty)")
    print("  2. Set LOCAL_MODEL_URL=http://localhost:8081/v1")
    print("  3. Restart the backend server")
    print("\nRecommended follow-up:")
    print("  - Warm the parser prefixes: .\\venv\\Scripts\\python.exe scripts\\warm_local_llm.py")
    print("  - Benchmark the real parser: .\\venv\\Scripts\\python.exe scripts\\benchmark_order_parser.py")
    print("\nNote: Small models can work well for JSON if the prompt stays short and structured.")


if __name__ == "__main__":
    main()


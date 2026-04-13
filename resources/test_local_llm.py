"""Quick smoke-test for the local LLM integration.

Usage (after starting the sidecar):
    uv run python scripts/test_local_llm.py

Checks:
1. Server is reachable at LOCAL_LLM_URL
2. Chat completion works
3. JSON extraction from LLM response works
4. OpenAILLMEvaluator works with base_url
5. LLMFormFiller works with base_url
"""

from __future__ import annotations

import json
import os
import sys

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

LOCAL_URL = os.environ.get("JOBHUNTER_LOCAL_LLM_URL", "http://localhost:8080/v1")


def check_server():
    """Step 1: Check the server is up."""
    print(f"\n{'='*60}")
    print(f"1️⃣  Checking server at {LOCAL_URL}")
    print(f"{'='*60}")

    from openai import OpenAI

    client = OpenAI(base_url=LOCAL_URL, api_key="local")
    models = client.models.list()
    print(f"   ✅ Server is up! Models: {[m.id for m in models.data]}")
    return client


def check_chat(client):
    """Step 2: Simple chat completion."""
    print(f"\n{'='*60}")
    print("2️⃣  Testing chat completion")
    print(f"{'='*60}")

    resp = client.chat.completions.create(
        model="local",
        messages=[
            {"role": "system", "content": "You are a helpful assistant. Reply in one short sentence."},
            {"role": "user", "content": "What is Python?"},
        ],
        temperature=0.1,
        max_tokens=100,
    )
    text = resp.choices[0].message.content
    print(f"   Response: {text}")
    print(f"   Tokens: {resp.usage.completion_tokens} completion, {resp.usage.total_tokens} total")
    print("   ✅ Chat completion works!")


def check_json_output(client):
    """Step 3: JSON extraction (what our scoring pipeline needs)."""
    print(f"\n{'='*60}")
    print("3️⃣  Testing JSON output (scoring format)")
    print(f"{'='*60}")

    resp = client.chat.completions.create(
        model="local",
        messages=[
            {"role": "system", "content": (
                "You are a job-fit evaluator. Return ONLY a JSON object with these keys:\n"
                '{"fit_score": <int 0-100>, "missing_skills": [], "risk_flags": [], "decision": "apply|skip|review"}\n'
                "Return ONLY valid JSON. No markdown, no commentary."
            )},
            {"role": "user", "content": (
                "RESUME: Python developer with 5 years experience in FastAPI, AWS, Docker.\n"
                "JOB: Senior Python Developer at TechCorp. Requires Python, FastAPI, Kubernetes."
            )},
        ],
        temperature=0.1,
        max_tokens=300,
    )
    raw = resp.choices[0].message.content
    print(f"   Raw response:\n   {raw}")

    from job_hunter.llm_client import safe_json_parse

    try:
        data = safe_json_parse(raw)
        print(f"   Parsed: fit_score={data.get('fit_score')}, decision={data.get('decision')}")
        print("   ✅ JSON extraction works!")
    except Exception as e:
        print(f"   ⚠️  JSON parse failed: {e}")
        print("   This is expected with very small models. 3B+ recommended for JSON.")


def check_evaluator():
    """Step 4: Test OpenAILLMEvaluator with base_url."""
    print(f"\n{'='*60}")
    print("4️⃣  Testing OpenAILLMEvaluator with local LLM")
    print(f"{'='*60}")

    from job_hunter.matching.llm_eval import OpenAILLMEvaluator

    evaluator = OpenAILLMEvaluator(
        api_key="local",
        model="local",
        base_url=LOCAL_URL,
    )
    try:
        result = evaluator.evaluate(
            resume="Python developer, 5 years, FastAPI, AWS, Docker",
            job_description="Senior Python Developer. Requirements: Python, FastAPI, Kubernetes, AWS.",
        )
        print(f"   fit_score: {result['fit_score']}")
        print(f"   decision:  {result['decision']}")
        print(f"   missing:   {result['missing_skills']}")
        print(f"   risks:     {result['risk_flags']}")
        print("   ✅ LLM Evaluator works!")
    except Exception as e:
        print(f"   ⚠️  Evaluator failed: {e}")
        print("   Small models may struggle with structured output. Try a 3B+ model.")


def check_form_filler():
    """Step 5: Test LLMFormFiller with base_url."""
    print(f"\n{'='*60}")
    print("5️⃣  Testing LLMFormFiller with local LLM")
    print(f"{'='*60}")

    from job_hunter.linkedin.form_filler_llm import LLMFormFiller

    filler = LLMFormFiller(
        api_key="local",
        model="local",
        base_url=LOCAL_URL,
    )
    fields = [
        {"label": "Years of Python experience", "type": "number", "required": True},
        {"label": "Do you have AWS experience?", "type": "select", "options": ["Yes", "No"], "required": True},
    ]
    try:
        answers = filler.answer_fields(
            fields=fields,
            profile_context="Python developer with 5 years experience. Skills: Python, FastAPI, AWS, Docker.",
            job_context="Senior Python Developer role",
        )
        print(f"   Answers: {json.dumps(answers, indent=2)}")
        print("   ✅ Form filler works!")
    except Exception as e:
        print(f"   ⚠️  Form filler failed: {e}")


def main():
    print("🧪 Local LLM Integration Test")
    print(f"   Server URL: {LOCAL_URL}")

    try:
        client = check_server()
    except Exception as e:
        print(f"\n   ❌ Cannot reach server at {LOCAL_URL}")
        print(f"   Error: {e}")
        print("\n   Start the server first:")
        print("     uv run python -m llama_cpp.server --model models/model.gguf --port 8080")
        print("   Or via Docker:")
        print("     docker compose --profile local-llm up llm")
        sys.exit(1)

    check_chat(client)
    check_json_output(client)
    check_evaluator()
    check_form_filler()

    print(f"\n{'='*60}")
    print("🎉 All checks complete!")
    print(f"{'='*60}")
    print("\nTo use local LLM in the web UI:")
    print("  1. Go to Settings → LLM Provider → Local LLM")
    print("  2. Set URL to", LOCAL_URL)
    print("  3. Save and run scoring / market analysis")


if __name__ == "__main__":
    main()


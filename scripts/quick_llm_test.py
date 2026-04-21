"""Quick test of the currently running local LLM."""
import httpx, time, json, re, sys

URL = "http://localhost:8081/v1/chat/completions"

SYSTEM = """Parse radio messages into JSON. Respond with ONLY JSON.
Units: A-squad, B-squad, Mortar Section, Recon Team.
Grid: alphanumeric (A-J, 1-10), snail subdivision 1-9. Example: B8-2-4.

Output schema:
{"classification":"command|acknowledgment|status_request|status_report|unclear","language":"en|ru","target_unit_refs":["name"],"order_type":"move|attack|fire|defend|observe|disengage|halt|null","location_refs":[{"source_text":"text","ref_type":"snail|grid|coordinate","normalized":"B8-2-4"}],"speed":"slow|fast|null","confidence":0.0-1.0}"""

TESTS = [
    ("A-squad, move to B8-2-4 fast!", "command", "move"),
    ("Mortar Section, fire at D7-8!", "command", "fire"),
    ("Так точно, выполняем.", "acknowledgment", None),
    ("Recon Team, report status", "status_request", "report_status"),
    ("B-squad, атакуйте позицию противника в E5-3!", "command", "attack"),
]

print("Testing local LLM at", URL)
print("=" * 60)

total_time = 0
json_ok = 0
class_ok = 0

for msg, exp_class, exp_type in TESTS:
    print(f"\nMessage: \"{msg}\"")
    user = f'MESSAGE: "{msg}"\nPARSED:'

    start = time.time()
    try:
        r = httpx.post(URL, json={
            "model": "local",
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        }, timeout=180)
        elapsed = time.time() - start
        total_time += elapsed

        data = r.json()
        usage = data.get("usage", {})
        content = data["choices"][0]["message"]["content"]

        # Strip think blocks
        content_clean = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        # Strip markdown fences
        if content_clean.startswith("```"):
            nl = content_clean.find("\n")
            if nl != -1: content_clean = content_clean[nl+1:]
            if content_clean.endswith("```"): content_clean = content_clean[:-3].strip()

        prompt_tok = usage.get("prompt_tokens", 0)
        gen_tok = usage.get("completion_tokens", 0)
        speed = gen_tok / elapsed if elapsed > 0 else 0

        try:
            parsed = json.loads(content_clean)
            is_json = True
            json_ok += 1
            got_class = parsed.get("classification")
            got_type = parsed.get("order_type")
        except json.JSONDecodeError:
            is_json = False
            got_class = None
            got_type = None

        c_match = got_class == exp_class
        if c_match: class_ok += 1

        status = "OK" if (is_json and c_match) else "FAIL"
        print(f"  [{status}] {elapsed:.1f}s | {prompt_tok}p+{gen_tok}g tok | {speed:.1f} tok/s")
        print(f"  Class: {got_class} (exp: {exp_class}), Type: {got_type} (exp: {exp_type})")
        if not is_json:
            print(f"  JSON INVALID! Raw: {content_clean[:200]}")
    except Exception as e:
        elapsed = time.time() - start
        total_time += elapsed
        print(f"  ERROR ({elapsed:.1f}s): {e}")

print(f"\n{'='*60}")
print(f"Total: {total_time:.1f}s | JSON valid: {json_ok}/{len(TESTS)} | Classification: {class_ok}/{len(TESTS)}")
print(f"Avg: {total_time/len(TESTS):.1f}s per request")


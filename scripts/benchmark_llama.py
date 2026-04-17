"""Quick benchmark for Llama 3.2 1B on order parsing tasks."""
import time, json, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openai import OpenAI

URL = os.environ.get("LOCAL_MODEL_URL", "http://localhost:8081/v1")
MODEL = os.environ.get("LOCAL_MODEL_NAME", "local")
client = OpenAI(base_url=URL, api_key="local")

tests = [
    ("EN command", "A-squad, move to B8-2-4 fast!", "command", "move"),
    ("RU command", "\u041f\u0435\u0440\u0432\u044b\u0439 \u0432\u0437\u0432\u043e\u0434, \u0430\u0442\u0430\u043a\u0443\u0439\u0442\u0435 \u043f\u0440\u043e\u0442\u0438\u0432\u043d\u0438\u043a\u0430 \u0432 E5-3!", "command", "attack"),
    ("EN ack", "Roger, moving out.", "acknowledgment", None),
    ("RU report", "\u041d\u0430 \u043f\u043e\u0437\u0438\u0446\u0438\u0438, \u043f\u043e\u0442\u0435\u0440\u044c \u043d\u0435\u0442.", "status_report", None),
    ("Complex", "Advance to F8-1-9. Eliminate any enemy forces inbound. Use wedge formation.", "command", "attack"),
]

SYSTEM = (
    "You are a military radio message parser.\n"
    "Classify the message and extract details into JSON.\n\n"
    "Rules:\n"
    "- classification: command, acknowledgment, or status_report\n"
    "- order_type: move, attack, defend, observe, fire, disengage, or resupply (null if not a command)\n"
    "- target_unit_refs: list of unit names mentioned\n"
    "- location_refs: list of grid references or coordinates\n"
    "- speed: slow, fast, or null\n"
    "- confidence: number 0.0 to 1.0\n\n"
    "Examples:\n\n"
    "Input: '1st Platoon, move to B4-3 fast!'\n"
    'Output: {"classification": "command", "order_type": "move", '
    '"target_unit_refs": ["1st Platoon"], "location_refs": ["B4-3"], '
    '"speed": "fast", "confidence": 0.9}\n\n'
    "Input: 'Attack enemy position at D7-8!'\n"
    'Output: {"classification": "command", "order_type": "attack", '
    '"target_unit_refs": [], "location_refs": ["D7-8"], '
    '"speed": null, "confidence": 0.9}\n\n'
    "Input: 'Roger, moving out.'\n"
    'Output: {"classification": "acknowledgment", "order_type": null, '
    '"target_unit_refs": [], "location_refs": [], '
    '"speed": null, "confidence": 0.95}\n\n'
    "Input: 'In position, no casualties.'\n"
    'Output: {"classification": "status_report", "order_type": null, '
    '"target_unit_refs": [], "location_refs": [], '
    '"speed": null, "confidence": 0.95}\n\n'
    "Return ONLY the JSON object. No explanation, no markdown."
)

print(f"Benchmarking: {URL} model={MODEL}")
print(f"{'Test':12s} | {'Time':>5s} | {'Tok':>3s} | {'T/s':>5s} | {'JSON':4s} | {'Class':5s} | Result")
print("-" * 80)

ok_count = 0
json_ok = 0
total_time = 0

for label, msg, exp_cls, exp_type in tests:
    t0 = time.time()
    r = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"RADIO: {msg}"},
        ],
        temperature=0.1,
        max_tokens=200,
    )
    dt = time.time() - t0
    total_time += dt
    raw = r.choices[0].message.content
    toks = r.usage.completion_tokens
    tps = toks / dt if dt > 0 else 0

    # Parse JSON
    try:
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
        d = json.loads(cleaned)
        j_ok = "OK"
        json_ok += 1
        cls = d.get("classification", "?")
        ot = d.get("order_type")
    except Exception:
        j_ok = "FAIL"
        cls = "?"
        ot = "?"

    cls_ok = cls == exp_cls
    type_ok = (exp_type is None) or (ot == exp_type)
    if cls_ok and type_ok:
        ok_count += 1
    result = f"cls={cls} type={ot}"
    mark = "PASS" if (cls_ok and type_ok and j_ok == "OK") else "MISS"
    print(f"{label:12s} | {dt:5.1f}s | {toks:3d} | {tps:5.1f} | {j_ok:4s} | {mark:5s} | {result}")

print("-" * 80)
print(f"JSON valid: {json_ok}/{len(tests)} | Correct: {ok_count}/{len(tests)} | Total time: {total_time:.1f}s | Avg: {total_time/len(tests):.1f}s")




import sys, json, os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ".")
from backend.services.order_parser import _repair_json, _fixup_llm_json

results = []
tests = [
    '{"classification":"command","order_type":"fire","status_request_focus":"full","location_refs":[{"source_text":"pos","ref_type":"contact","normalized":"',
    '{"a":"b","c":[{"d":"e"}],"f":',
    '{"classification":"command","ambiguities":{}}',
]
for i, t in enumerate(tests):
    r = _repair_json(t)
    try:
        d = json.loads(r)
        _fixup_llm_json(d)
        results.append(f"Test {i+1} OK: {list(d.keys())}")
    except Exception as e:
        results.append(f"Test {i+1} FAIL: {e}\nRepaired: {r[:200]}")

with open("scripts/test_json_repair_result.txt", "w") as f:
    f.write("\n".join(results) + "\nALL DONE\n")

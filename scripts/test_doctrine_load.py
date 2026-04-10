import sys, os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding='utf-8')

results = []
try:
    from backend.prompts.tactical_doctrine import TACTICAL_DOCTRINE_FULL, TACTICAL_DOCTRINE_BRIEF
    results.append(f"FULL: {len(TACTICAL_DOCTRINE_FULL)} chars")
    results.append(f"BRIEF: {len(TACTICAL_DOCTRINE_BRIEF)} chars")
    results.append(f"FALLBACK in full: {'FALLBACK' in TACTICAL_DOCTRINE_FULL}")
    results.append(f"Has Concentration: {'Concentration' in TACTICAL_DOCTRINE_FULL}")
    results.append(f"Has Obstacle: {'Obstacle' in TACTICAL_DOCTRINE_FULL}")
    results.append(f"Has METT: {'METT' in TACTICAL_DOCTRINE_FULL}")
    results.append(f"Brief has Combined: {'Combined' in TACTICAL_DOCTRINE_BRIEF}")
    results.append(f"First 80 chars: {repr(TACTICAL_DOCTRINE_FULL[:80])}")
    results.append("SUCCESS")
except Exception as e:
    results.append(f"ERROR: {e}")
    import traceback
    results.append(traceback.format_exc())

out_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_doctrine_test.txt")
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(results))



"""Quick check that tactical doctrine integration works."""
import sys
try:
    from backend.prompts.tactical_doctrine import get_tactical_doctrine
    full = get_tactical_doctrine("full")
    brief = get_tactical_doctrine("brief")
    print(f"Full: {len(full)} chars, Brief: {len(brief)} chars")

    from backend.prompts.red_commander import build_red_commander_prompt
    from backend.services.red_ai.doctrine import get_doctrine
    d = get_doctrine("balanced")
    s, u = build_red_commander_prompt(
        {"name": "Test", "risk_posture": "balanced"},
        d,
        {"type": "hold"},
        {"own_units": [], "known_contacts": [], "summary": {},
         "terrain_around_units": {}, "elevation_at_units": {},
         "terrain_types_present": [], "discovered_objects": []},
        tick=1
    )
    assert "TACTICAL DOCTRINE" in s
    assert "TACTICAL ANALYSIS" in u
    print(f"Red AI prompt: sys={len(s)}, usr={len(u)}")

    from backend.prompts.order_parser import SYSTEM_PROMPT
    assert "Tactical Reference" in SYSTEM_PROMPT
    print(f"Order parser: {len(SYSTEM_PROMPT)} chars")

    from backend.services.red_ai.agent import red_ai_agent
    assert hasattr(red_ai_agent, '_decide_recon')
    assert hasattr(red_ai_agent, '_decide_artillery_support')
    print("Agent methods OK")

    print("\nALL OK")
except Exception as e:
    print(f"FAILED: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)


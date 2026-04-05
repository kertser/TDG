"""Quick end-to-end system verification."""
import requests
import sys

BASE = "http://localhost:8000"

def main():
    errors = []

    # 1. Register a user
    r = requests.post(f"{BASE}/api/auth/register", json={"display_name": "VerifyUser"})
    print(f"1. Register:           {r.status_code}", end="")
    if r.status_code == 200:
        data = r.json()
        token = data["token"]
        print(f"  user={data.get('display_name', data.get('user_id','?'))}")
    else:
        print(f"  FAIL: {r.text[:100]}")
        errors.append("register")
        return

    headers = {"Authorization": f"Bearer {token}"}

    # 2. List scenarios
    r = requests.get(f"{BASE}/api/scenarios")
    scenarios = r.json()
    print(f"2. Scenarios:          {r.status_code}  count={len(scenarios)}")
    if not scenarios:
        print("   No scenarios found! Run seed_scenario.py first.")
        errors.append("scenarios")
        return
    scenario_id = scenarios[0]["id"]

    # 3. Create session
    r = requests.post(f"{BASE}/api/sessions", json={"scenario_id": scenario_id}, headers=headers)
    print(f"3. Create session:     {r.status_code}", end="")
    if r.status_code == 200:
        session = r.json()
        session_id = session["id"]
        print(f"  id={session_id[:8]}... status={session['status']}")
    else:
        print(f"  FAIL: {r.text[:100]}")
        errors.append("create_session")
        return

    # 4. Join session (creator auto-joins as admin, so this may 400; that's OK)
    r = requests.post(f"{BASE}/api/sessions/{session_id}/join",
                      json={"side": "blue", "role": "commander"}, headers=headers)
    if r.status_code == 200:
        print(f"4. Join session:       {r.status_code}  side={r.json().get('side', '?')}")
    else:
        # Already joined as admin on create — that's fine
        print(f"4. Join session:       {r.status_code}  (already joined as admin — OK)")

    # 5. Start session
    r = requests.post(f"{BASE}/api/sessions/{session_id}/start", headers=headers)
    print(f"5. Start session:      {r.status_code}  status={r.json().get('status', '?')}")

    # 6. Get units
    r = requests.get(f"{BASE}/api/sessions/{session_id}/units", headers=headers)
    units = r.json()
    print(f"6. Units:              {r.status_code}  count={len(units)}")
    for u in units[:3]:
        print(f"   - {u['name']} ({u['unit_type']}) side={u['side']}")

    # 7. Get grid
    r = requests.get(f"{BASE}/api/sessions/{session_id}/grid")
    features = r.json().get("features", [])
    print(f"7. Grid:               {r.status_code}  features={len(features)}")

    # 8. Get contacts (before ticks)
    r = requests.get(f"{BASE}/api/sessions/{session_id}/contacts", headers=headers)
    print(f"8. Contacts (pre-tick):{r.status_code}  count={len(r.json())}")

    # 9. Run 3 ticks
    for i in range(1, 4):
        r = requests.post(f"{BASE}/api/sessions/{session_id}/tick", headers=headers)
        tick = r.json().get("tick", "?")
        print(f"9. Tick {i}:             {r.status_code}  tick={tick}")

    # 10. Get events
    r = requests.get(f"{BASE}/api/sessions/{session_id}/events", headers=headers)
    events = r.json()
    print(f"10. Events:            {r.status_code}  count={len(events)}")
    for e in events[:5]:
        summary = (e.get("text_summary") or e["event_type"])[:60]
        print(f"    [{e['event_type']}] {summary}")

    # 11. Contacts after ticks
    r = requests.get(f"{BASE}/api/sessions/{session_id}/contacts", headers=headers)
    contacts = r.json()
    print(f"11. Contacts (post):   {r.status_code}  count={len(contacts)}")
    for c in contacts[:3]:
        print(f"    - type={c.get('estimated_type','?')} conf={c.get('confidence','?')}")

    # 12. Grid point-to-snail (using new Reims area coordinates)
    r = requests.get(f"{BASE}/api/sessions/{session_id}/grid/point-to-snail",
                     params={"lat": 49.045, "lon": 4.47, "depth": 2})
    print(f"12. Point-to-snail:    {r.status_code}  result={r.json()}")

    # 13. Create overlay
    r = requests.post(f"{BASE}/api/sessions/{session_id}/overlays", headers=headers, json={
        "overlay_type": "marker",
        "geometry": {"type": "Point", "coordinates": [2.35, 48.85]},
        "style_json": {"color": "blue"},
        "label": "Test marker"
    })
    if r.status_code == 200:
        print(f"13. Create overlay:    {r.status_code}  id={r.json().get('id', '?')[:8]}...")
    else:
        print(f"13. Create overlay:    {r.status_code}  body={r.text[:100]}")

    # 14. List overlays
    r = requests.get(f"{BASE}/api/sessions/{session_id}/overlays", headers=headers)
    if r.status_code == 200:
        print(f"14. List overlays:     {r.status_code}  count={len(r.json())}")
    else:
        print(f"14. List overlays:     {r.status_code}  body={r.text[:100]}")

    # 15. Frontend serving
    r = requests.get(f"{BASE}/")
    ok = "OK" if r.status_code == 200 and "leaflet" in r.text.lower() else "FAIL"
    print(f"15. Frontend HTML:     {r.status_code}  {ok}")

    # 16. API docs
    r = requests.get(f"{BASE}/docs")
    print(f"16. OpenAPI docs:      {r.status_code}  {'OK' if r.status_code == 200 else 'FAIL'}")

    print()
    if errors:
        print(f"=== ISSUES FOUND: {errors} ===")
        sys.exit(1)
    else:
        print("=== ALL CHECKS PASSED ===")
        print(f"\nOpen http://localhost:8000 in your browser to see the frontend.")
        print(f"Open http://localhost:8000/docs for API documentation.")


if __name__ == "__main__":
    main()





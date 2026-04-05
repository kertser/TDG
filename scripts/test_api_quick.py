"""Quick API integration test."""
import httpx

BASE = "http://localhost:8000"

# 1. Register
r = httpx.post(f"{BASE}/api/auth/register", json={"display_name": "Test Player"})
assert r.status_code == 200, f"Register failed: {r.text}"
auth = r.json()
token = auth["token"]
headers = {"Authorization": f"Bearer {token}"}
print(f"✅ Registered: {auth['display_name']} (id={auth['user_id'][:8]}...)")

# 2. List sessions
r = httpx.get(f"{BASE}/api/sessions")
sessions = r.json()
print(f"✅ Sessions: {len(sessions)} found")
session_id = sessions[0]["id"]

# 3. Join session
r = httpx.post(f"{BASE}/api/sessions/{session_id}/join",
               json={"side": "blue", "role": "commander"}, headers=headers)
print(f"✅ Joined session: {r.json()}")

# 4. Get grid
r = httpx.get(f"{BASE}/api/sessions/{session_id}/grid?depth=0")
grid = r.json()
print(f"✅ Grid loaded: {len(grid['features'])} squares")
sample = grid['features'][0]
print(f"   First square: {sample['properties']['label']}")

# 5. Point-to-snail
r = httpx.get(f"{BASE}/api/sessions/{session_id}/grid/point-to-snail",
              params={"lat": 48.84, "lon": 2.335, "depth": 2})
snail = r.json()
print(f"✅ Point-to-snail: ({48.84}, {2.335}) → {snail['snail_path']}")

# 6. Snail-to-geometry
r = httpx.get(f"{BASE}/api/sessions/{session_id}/grid/snail-to-geometry",
              params={"path": snail['snail_path']})
geom = r.json()
print(f"✅ Snail-to-geometry: {snail['snail_path']} → polygon with {len(geom['geometry']['coordinates'][0])} points")

# 7. Get units
r = httpx.get(f"{BASE}/api/sessions/{session_id}/units", headers=headers)
units = r.json()
print(f"✅ Units: {len(units)} visible (blue side)")

# 8. Submit order
r = httpx.post(f"{BASE}/api/sessions/{session_id}/orders",
               json={"original_text": "1st Platoon move to B4-3"},
               headers=headers)
order = r.json()
print(f"✅ Order submitted: id={order['id'][:8]}... status={order['status']}")

# 9. Get session details
r = httpx.get(f"{BASE}/api/sessions/{session_id}")
session = r.json()
print(f"✅ Session: status={session['status']}, tick={session['tick']}, players={session['participant_count']}")

print("\n🎉 All API tests passed!")


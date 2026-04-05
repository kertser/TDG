"""Test the full simulation tick flow via the API."""
import httpx

base = 'http://localhost:8000'

# ── Auth ─────────────────────────────────────────────
r = httpx.post(f'{base}/api/auth/register', json={'display_name': 'TickTest'})
if r.status_code != 200:
    r = httpx.post(f'{base}/api/auth/login', json={'display_name': 'TickTest'})
data = r.json()
token = data['token']
headers = {'Authorization': f'Bearer {token}'}
print(f'Auth OK: {data["display_name"]}')

# ── Get scenario ────────────────────────────────────
r = httpx.get(f'{base}/api/scenarios')
scenarios = r.json()
scenario_id = scenarios[0]['id']

# ── Create session ──────────────────────────────────
r = httpx.post(f'{base}/api/sessions', json={'scenario_id': scenario_id}, headers=headers)
session = r.json()
session_id = session['id']
print(f'Session: {session_id[:8]}... status={session["status"]}')

# ── Join ────────────────────────────────────────────
r = httpx.post(
    f'{base}/api/sessions/{session_id}/join',
    json={'side': 'blue', 'role': 'commander'},
    headers=headers,
)

# ── Start ───────────────────────────────────────────
r = httpx.post(f'{base}/api/sessions/{session_id}/start', headers=headers)
print(f'Start: {r.json()}')

# ── Get initial units ───────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/units', headers=headers)
units = r.json()
print(f'\nInitial units: {len(units)}')
for u in units:
    print(f'  [{u["side"]}] {u["name"]} str={u.get("strength")} morale={u.get("morale")}')

# ── Run several ticks ───────────────────────────────
print('\n── Running simulation ticks ──')
for i in range(5):
    r = httpx.post(f'{base}/api/sessions/{session_id}/tick', headers=headers)
    if r.status_code != 200:
        print(f'Tick {i+1} FAILED: {r.status_code} {r.text}')
        break
    result = r.json()
    print(f'Tick {result["tick"]}: {result["events_count"]} events, {result["units_alive"]} units alive')

# ── Check events ────────────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/events', headers=headers)
events = r.json()
print(f'\nTotal events: {len(events)}')
for e in events[:10]:
    print(f'  [{e["event_type"]}] {e.get("text_summary", "")[:60]}')

# ── Check contacts ──────────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/contacts', headers=headers)
contacts = r.json()
print(f'\nContacts: {len(contacts)}')
for c in contacts:
    print(f'  {c.get("estimated_type", "?")} at ({c.get("lat"):.4f}, {c.get("lon"):.4f}) conf={c.get("confidence", 0):.2f}')

# ── Check units after ticks ─────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/units', headers=headers)
units_after = r.json()
print(f'\nUnits after ticks: {len(units_after)}')
for u in units_after:
    name = u["name"]
    strength = u.get("strength")
    morale = u.get("morale")
    supp = u.get("suppression")
    print(f'  [{u["side"]}] {name} str={strength} morale={morale} supp={supp}')

print('\n✅ Tick test complete!')


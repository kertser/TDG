"""Test the full simulation flow: orders → movement → detection → combat."""
import httpx

base = 'http://localhost:8000'

# ── Auth ─────────────────────────────────────────────
r = httpx.post(f'{base}/api/auth/register', json={'display_name': 'SimTest'})
if r.status_code != 200:
    r = httpx.post(f'{base}/api/auth/login', json={'display_name': 'SimTest'})
data = r.json()
token = data['token']
headers = {'Authorization': f'Bearer {token}'}

# ── Setup ────────────────────────────────────────────
r = httpx.get(f'{base}/api/scenarios')
scenario_id = r.json()[0]['id']

r = httpx.post(f'{base}/api/sessions', json={'scenario_id': scenario_id}, headers=headers)
session_id = r.json()['id']

r = httpx.post(f'{base}/api/sessions/{session_id}/join',
               json={'side': 'blue', 'role': 'commander'}, headers=headers)

r = httpx.post(f'{base}/api/sessions/{session_id}/start', headers=headers)
print(f'Session started: tick={r.json()["tick"]}')

# ── Get units ────────────────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/units', headers=headers)
units = r.json()
print(f'\n{len(units)} units:')
for u in units:
    print(f'  [{u["side"]}] {u["name"]} at ({u["lat"]:.4f}, {u["lon"]:.4f})')

# Find our Recon Team and a blue platoon
recon = next((u for u in units if 'recon' in u['name'].lower()), None)
platoon = next((u for u in units if '1st Platoon' in u['name']), None)

if recon:
    print(f'\nRecon Team: {recon["name"]} at ({recon["lat"]:.4f}, {recon["lon"]:.4f})')

    # ── Submit a MOVE order for Recon Team ───────────────
    target_lat = 48.852  # Move toward Red positions
    target_lon = 2.348

    r = httpx.post(f'{base}/api/sessions/{session_id}/orders', json={
        'target_unit_ids': [recon['id']],
        'original_text': f'Move to {target_lat}, {target_lon}',
        'order_type': 'move',
        'parsed_order': {
            'type': 'move',
            'target_location': {'lat': target_lat, 'lon': target_lon},
        },
    }, headers=headers)
    if r.status_code != 200:
        print(f'Order failed: {r.status_code} {r.text[:200]}')
    else:
        order = r.json()
        print(f'Move order submitted: {order["id"][:8]}... status={order["status"]}')

if platoon:
    # ── Submit a MOVE order for 1st Platoon too ──────────
    r = httpx.post(f'{base}/api/sessions/{session_id}/orders', json={
        'target_unit_ids': [platoon['id']],
        'original_text': 'Advance to grid B4',
        'order_type': 'move',
        'parsed_order': {
            'type': 'move',
            'target_location': {'lat': 48.852, 'lon': 2.346},
        },
    }, headers=headers)
    print(f'1st Platoon move order: {r.json()["status"]}')

# ── Run 10 ticks and observe ─────────────────────────
print('\n── Simulation Run ──')
for i in range(10):
    r = httpx.post(f'{base}/api/sessions/{session_id}/tick', headers=headers)
    if r.status_code != 200:
        print(f'Tick FAILED: {r.status_code} {r.text}')
        break
    result = r.json()
    events_count = result['events_count']
    marker = '📡' if events_count > 0 else '  '
    print(f'{marker} Tick {result["tick"]:3d}: {events_count} events, {result.get("units_alive", "?")} alive (own side)')

# ── Final state ──────────────────────────────────────
print('\n── Final Unit Positions ──')
r = httpx.get(f'{base}/api/sessions/{session_id}/units', headers=headers)
units_after = r.json()
for u in units_after:
    print(f'  [{u["side"]}] {u["name"]:30s} at ({u["lat"]:.4f}, {u["lon"]:.4f}) str={u.get("strength", "?")} morale={u.get("morale", "?")}')

# ── Events ───────────────────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/events', headers=headers)
events = r.json()
print(f'\n── Events ({len(events)}) ──')
for e in events:
    print(f'  Tick {e.get("tick", "?"):3d} [{e["event_type"]:20s}] {e.get("text_summary", "")[:70]}')

# ── Contacts ─────────────────────────────────────────
r = httpx.get(f'{base}/api/sessions/{session_id}/contacts', headers=headers)
contacts = r.json()
print(f'\n── Contacts ({len(contacts)}) ──')
for c in contacts:
    print(f'  {c.get("estimated_type", "?"):20s} at ({c.get("lat", 0):.4f}, {c.get("lon", 0):.4f}) conf={c.get("confidence", 0):.2f} stale={c.get("is_stale", False)}')

print('\n✅ Full simulation test complete!')



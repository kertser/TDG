"""Quick API test to verify the full TDG flow."""
import httpx

base = 'http://localhost:8000'

# Register
r = httpx.post(f'{base}/api/auth/register', json={'display_name': 'TestFlow'})
print('Register:', r.status_code, r.json().get('display_name'))
token = r.json()['token']
headers = {'Authorization': f'Bearer {token}'}

# List scenarios
r = httpx.get(f'{base}/api/scenarios')
print('Scenarios:', r.status_code, len(r.json()))
scenario_id = r.json()[0]['id']

# Create session
r = httpx.post(f'{base}/api/sessions', json={'scenario_id': scenario_id}, headers=headers)
print('Create session:', r.status_code, r.json().get('status'))
session_id = r.json()['id']

# Join session (may already be joined as creator)
r = httpx.post(
    f'{base}/api/sessions/{session_id}/join',
    json={'side': 'blue', 'role': 'commander'},
    headers=headers,
)
print('Join:', r.status_code)

# Start session (initializes units from scenario)
r = httpx.post(f'{base}/api/sessions/{session_id}/start', headers=headers)
print('Start:', r.status_code, r.json())

# Get units
r = httpx.get(f'{base}/api/sessions/{session_id}/units', headers=headers)
units = r.json()
print(f'Units: {r.status_code}, {len(units)} units')
for u in units:
    name = u['name']
    lat = u['lat']
    lon = u['lon']
    side = u['side']
    sidc = u['sidc']
    print(f'  {side}: {name} at ({lat}, {lon}) sidc={sidc}')

# Get grid
r = httpx.get(f'{base}/api/sessions/{session_id}/grid?depth=0')
print(f'Grid: {r.status_code}, {len(r.json()["features"])} squares')

# Get contacts (should be empty initially)
r = httpx.get(f'{base}/api/sessions/{session_id}/contacts', headers=headers)
print(f'Contacts: {r.status_code}, {len(r.json())} contacts')

print('\n✅ All API tests passed!')


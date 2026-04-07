"""Verify SIDC codes are correct."""
import json

ECHELON_NAMES = {
    '00': 'Unspecified', '11': 'Team/Crew', '12': 'Squad', '13': 'Section',
    '14': 'Platoon', '15': 'Company', '16': 'Battalion', '17': 'Regiment',
    '18': 'Brigade', '21': 'Division'
}
HQ_NAMES = {'0': 'None', '1': 'Feint', '2': 'HQ', '3': 'Feint HQ', '4': 'TF', '6': 'TF HQ'}
SI_NAMES = {'3': 'Friend', '6': 'Hostile'}

with open('frontend/config/unit_types.json') as f:
    data = json.load(f)

header = f"{'Key':35s} {'Label':22s} {'SI':8s} {'HQ':5s} {'Ech':12s} Entity  Mod"
print(header)
print('-' * 100)
for key, val in data.items():
    if key.startswith('_'):
        continue
    s = val['sidc_blue']
    si = SI_NAMES.get(s[3], '?')
    hq = HQ_NAMES.get(s[7], '?')
    ech = ECHELON_NAMES.get(s[8:10], '?')
    entity = s[10:16]
    mod = s[16:20]
    ok_marker = ''
    if 'team' in key and ech != 'Team/Crew':
        ok_marker = ' ← CHECK'
    if 'squad' in key and ech != 'Squad':
        ok_marker = ' ← CHECK'
    if 'section' in key and ech != 'Section':
        ok_marker = ' ← CHECK'
    if 'platoon' in key and ech != 'Platoon':
        ok_marker = ' ← CHECK'
    if 'company' in key and ech != 'Company':
        ok_marker = ' ← CHECK'
    if 'battery' in key and ech != 'Company':
        ok_marker = ' ← CHECK'
    if 'battalion' in key and ech != 'Battalion':
        ok_marker = ' ← CHECK'
    if 'vehicle' in key and ech != 'Team/Crew':
        ok_marker = ' ← CHECK'
    label = val['label']
    print(f"{key:35s} {label:22s} {si:8s} {hq:5s} {ech:12s} {entity}  {mod}{ok_marker}")


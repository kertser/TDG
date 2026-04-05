"""Test new viewport and subdivide API endpoints."""
import httpx

BASE = "http://localhost:8000"

r = httpx.get(f"{BASE}/api/sessions")
sessions = r.json()
sid = sessions[0]["id"]

# Grid meta
r = httpx.get(f"{BASE}/api/sessions/{sid}/grid/meta")
meta = r.json()
print(f"Grid meta: {meta['columns']}x{meta['rows']}, {meta['base_square_size_m']}m, scheme={meta['labeling_scheme']}")

# Viewport depth 1
r = httpx.get(f"{BASE}/api/sessions/{sid}/grid/viewport", params={
    "south": 48.835, "west": 2.325, "north": 48.845, "east": 2.340, "depth": 1
})
vp = r.json()
print(f"Viewport depth 1: {vp['meta']['visible_squares']} squares, {vp['meta']['total_cells']} cells")

# Viewport depth 2
r = httpx.get(f"{BASE}/api/sessions/{sid}/grid/viewport", params={
    "south": 48.835, "west": 2.325, "north": 48.845, "east": 2.340, "depth": 2
})
vp2 = r.json()
print(f"Viewport depth 2: {vp2['meta']['visible_squares']} squares, {vp2['meta']['total_cells']} cells")

# Viewport depth 3 (small area)
r = httpx.get(f"{BASE}/api/sessions/{sid}/grid/viewport", params={
    "south": 48.835, "west": 2.325, "north": 48.840, "east": 2.335, "depth": 3
})
vp3 = r.json()
print(f"Viewport depth 3 (small): {vp3['meta']['visible_squares']} squares, {vp3['meta']['total_cells']} cells")

# Subdivide
r = httpx.get(f"{BASE}/api/sessions/{sid}/grid/subdivide", params={"path": "B2"})
sub = r.json()
labels = [f["properties"]["short_label"] for f in sub["features"]]
print(f"Subdivide B2 → short labels: {labels}")
full_labels = [f["properties"]["label"] for f in sub["features"]]
print(f"Subdivide B2 → full labels: {full_labels}")

print("\n✅ All new grid endpoints working!")


"""Quick test for GridService."""
from types import SimpleNamespace
from shapely.geometry import Point
from backend.services.grid_service import GridService

grid_def = SimpleNamespace(
    origin=Point(2.325, 48.835),
    orientation_deg=0,
    base_square_size_m=1000,
    columns=8,
    rows=8,
    labeling_scheme='alphanumeric',
    recursion_base=3,
    max_depth=3,
)

svc = GridService(grid_def)

# Test square_to_polygon
poly = svc.square_to_polygon('A1')
print(f'A1 polygon centroid: ({poly.centroid.y:.5f}, {poly.centroid.x:.5f})')

# Test point_to_snail round-trip
snail = svc.point_to_snail(48.84, 2.335, depth=2)
print(f'Point(48.84, 2.335) -> snail: {snail}')

# Test snail_to_center
center = svc.snail_to_center(snail)
print(f'Snail {snail} -> center: ({center.y:.5f}, {center.x:.5f})')

# Test validate
print(f'Validate A1-3-7: {svc.validate_snail("A1-3-7")}')
print(f'Validate Z99-3: {svc.validate_snail("Z99-3")}')

# Test grid_as_geojson
geojson = svc.grid_as_geojson(depth=0)
print(f'GeoJSON features count: {len(geojson["features"])}')

# Test subdivide
children = svc.subdivide("B2")
print(f'B2 children: {[c[0] for c in children]}')

print('\n✅ GridService all tests passed!')


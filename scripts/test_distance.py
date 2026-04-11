#!/usr/bin/env python
"""Quick distance calculation."""
import sys
sys.path.insert(0, ".")

from scripts.tactical_tests.base import grid_center, distance_between

center = grid_center('D', 5)
blue_start = grid_center('B', 4)
red_start = grid_center('G', 6)

print(f"Blue start (B4): {blue_start}", flush=True)
print(f"Red start (G6): {red_start}", flush=True)
print(f"D5 center: {center}", flush=True)
print(f"Distance Blue B4 to D5: {distance_between(*blue_start, *center):.0f}m", flush=True)
print(f"Distance Red G6 to D5: {distance_between(*red_start, *center):.0f}m", flush=True)
print(f"Distance Blue to Red: {distance_between(*blue_start, *red_start):.0f}m", flush=True)

"""
LocationResolver – deterministic resolution of location references.

NO LLM involvement. Takes extracted location text from ParsedOrderData
and resolves each to geographic coordinates using GridService, regex
patterns, and directional offset logic.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from backend.schemas.order import LocationRefRaw, ResolvedLocation

logger = logging.getLogger(__name__)

# Compass directions → bearing in degrees (clockwise from north)
DIRECTION_BEARINGS = {
    # English
    "north": 0, "n": 0,
    "northeast": 45, "ne": 45,
    "east": 90, "e": 90,
    "southeast": 135, "se": 135,
    "south": 180, "s": 180,
    "southwest": 225, "sw": 225,
    "west": 270, "w": 270,
    "northwest": 315, "nw": 315,
    # Russian
    "север": 0, "северн": 0, "северном": 0, "северного": 0,
    "северо-восток": 45, "северо-восточном": 45, "северо-восточн": 45,
    "восток": 90, "восточн": 90, "восточном": 90,
    "юго-восток": 135, "юго-восточном": 135, "юго-восточн": 135,
    "юг": 180, "южн": 180, "южном": 180,
    "юго-запад": 225, "юго-западном": 225, "юго-западн": 225,
    "запад": 270, "западн": 270, "западном": 270,
    "северо-запад": 315, "северо-западном": 315, "северо-западн": 315,
    # Relative (no absolute bearing — need context)
    "left": None, "слева": None, "лево": None, "левее": None,
    "right": None, "справа": None, "право": None, "правее": None,
    "flank_left": None, "flank_right": None,
    "forward": 0, "вперёд": 0, "вперед": 0,
    "back": 180, "назад": 180,
}

# Default offset distance for relative directions (meters)
DEFAULT_RELATIVE_OFFSET_M = 500

# Meters per degree (approximate at ~48° latitude)
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON = 74_000.0


class LocationResolver:
    """
    Deterministic resolution of location references to geographic coordinates.
    """

    def __init__(self, grid_service: Any = None, elevation_peaks: list[dict] | None = None,
                 map_objects: list[dict] | None = None):
        """
        Args:
            grid_service: An initialized GridService instance (for snail/grid resolution).
            elevation_peaks: List of elevation peak dicts with keys:
                {snail_path, lat, lon, elevation_m, label, label_ru}
            map_objects: List of map object dicts with keys:
                {object_type, name, lat, lon}
        """
        self.grid_service = grid_service
        self.elevation_peaks = elevation_peaks or []
        self.map_objects = map_objects or []

    def resolve_all(
        self,
        location_refs: list[LocationRefRaw],
        unit_position: tuple[float, float] | None = None,
        unit_heading_deg: float | None = None,
    ) -> list[ResolvedLocation]:
        """
        Resolve a list of location references.

        Args:
            location_refs: Raw location references from the parser.
            unit_position: (lat, lon) of the unit for relative references.
            unit_heading_deg: Unit's current heading for left/right resolution.

        Returns:
            List of ResolvedLocation with coordinates where possible.
        """
        results = []
        for ref in location_refs:
            resolved = self._resolve_one(ref, unit_position, unit_heading_deg)
            results.append(resolved)
        return results

    def _resolve_one(
        self,
        ref: LocationRefRaw,
        unit_pos: tuple[float, float] | None,
        heading_deg: float | None,
    ) -> ResolvedLocation:
        """Resolve a single location reference."""
        normalized = ref.normalized.strip()
        ref_type = ref.ref_type

        # 0. Contact-target reference — "на цель" / "at the target"
        # This is resolved later from known enemy contacts; we pass it through as-is.
        if ref_type == "contact_target":
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="contact_target",
                normalized_ref="nearest_enemy_contact",
                lat=None,
                lon=None,
                confidence=0.5,
            )

        # 1. Try snail path (e.g. "B8-2-4")
        if ref_type == "snail" or self._looks_like_snail(normalized):
            return self._resolve_snail(ref, normalized)

        # 2. Try grid square (e.g. "B8")
        if ref_type == "grid" or self._looks_like_grid(normalized):
            return self._resolve_grid(ref, normalized)

        # 3. Try coordinate (e.g. "48.85,2.35")
        if ref_type == "coordinate" or self._looks_like_coordinate(normalized):
            return self._resolve_coordinate(ref, normalized)

        # 3b. Try height/elevation reference (e.g. "height 170", "высота 170")
        if ref_type == "height" or self._looks_like_height(normalized):
            return self._resolve_height(ref, normalized)

        # 3c. Try named map object reference (e.g. "airfield", "bridge")
        if ref_type == "map_object":
            map_obj_result = self._resolve_map_object(ref, normalized)
            if map_obj_result and map_obj_result.lat is not None:
                return map_obj_result

        # 4. Try relative direction
        if ref_type == "relative":
            return self._resolve_relative(ref, normalized, unit_pos, heading_deg)

        # 4b. Try named map object (e.g. "Airfield", "Bridge", "supply cache")
        map_obj_result = self._resolve_map_object(ref, normalized)
        if map_obj_result and map_obj_result.lat is not None:
            return map_obj_result

        # 5. Try to auto-detect from source_text
        return self._resolve_from_source(ref, unit_pos, heading_deg)

    def _looks_like_snail(self, text: str) -> bool:
        return bool(re.match(r'^[A-Za-z]\d+(-\d){1,3}$', text))

    def _looks_like_grid(self, text: str) -> bool:
        return bool(re.match(r'^[A-Za-z]\d{1,2}$', text))

    def _looks_like_coordinate(self, text: str) -> bool:
        return bool(re.match(r'^-?\d+\.?\d*\s*[,;]\s*-?\d+\.?\d*$', text))

    def _looks_like_height(self, text: str) -> bool:
        """Check if text looks like a height/elevation reference."""
        text_lower = text.lower().strip()
        # English: "height 170", "hill 250", "elevation 300"
        # Russian: "высота 170", "выс. 170", "выс 170"
        return bool(re.match(
            r'^(?:height|hill|elevation|высота|выс\.?|отм\.?)\s*\d+',
            text_lower
        ))

    def _resolve_height(self, ref: LocationRefRaw, normalized: str) -> ResolvedLocation:
        """Resolve a height reference like 'height 170' or 'высота 170' to peak coordinates."""
        # Extract the elevation number
        m = re.search(r'(\d+(?:\.\d+)?)', normalized)
        if not m:
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="height",
                normalized_ref=normalized,
                confidence=0.1,
            )

        target_elev = float(m.group(1))

        if not self.elevation_peaks:
            logger.warning("No elevation peaks available for height resolution: %r", normalized)
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="height",
                normalized_ref=f"Height {int(target_elev)}",
                confidence=0.2,
            )

        # Find the closest peak by elevation value
        best_peak = None
        best_diff = float('inf')
        for peak in self.elevation_peaks:
            diff = abs(peak.get("elevation_m", 0) - target_elev)
            if diff < best_diff:
                best_diff = diff
                best_peak = peak

        if best_peak is None or best_diff > 50:
            # No peak within 50m of the target elevation
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="height",
                normalized_ref=f"Height {int(target_elev)}",
                confidence=0.2,
            )

        confidence = 0.9 if best_diff < 5 else (0.7 if best_diff < 20 else 0.5)

        return ResolvedLocation(
            source_text=ref.source_text,
            ref_type="height",
            normalized_ref=f"Height {round(best_peak['elevation_m'])}",
            lat=best_peak.get("lat"),
            lon=best_peak.get("lon"),
            confidence=confidence,
            resolution_depth=best_peak.get("snail_path", "").count("-") if best_peak.get("snail_path") else 0,
        )

    def _resolve_map_object(self, ref: LocationRefRaw, normalized: str) -> ResolvedLocation | None:
        """Try to resolve text as a named map object (e.g. 'Airfield', 'Bridge', 'fuel depot')."""
        if not self.map_objects:
            return None

        search_text = normalized.lower().strip()
        # Also check source_text for better matching
        source_lower = (ref.source_text or "").lower().strip()

        # Map object type keywords for matching
        TYPE_KEYWORDS = {
            "airfield": ["airfield", "аэродром", "впп", "runway"],
            "bridge_structure": ["bridge", "мост"],
            "fuel_depot": ["fuel depot", "fuel", "топлив", "склад горюч", "горючее", "заправк"],
            "supply_cache": ["supply", "cache", "склад", "запас"],
            "field_hospital": ["hospital", "госпиталь", "медпункт", "медсанбат"],
            "command_post_structure": ["command post", "кп", "командный пункт"],
            "observation_tower": ["tower", "вышка", "наблюдательн"],
            "pillbox": ["pillbox", "дот", "дзот", "bunker"],
            "roadblock": ["roadblock", "блокпост", "заграждение", "кпп"],
        }

        best_obj = None
        best_score = 0

        for obj in self.map_objects:
            if obj.get("lat") is None:
                continue

            score = 0
            obj_type = (obj.get("object_type") or "").lower()
            obj_name = (obj.get("name") or "").lower()

            # Direct name match
            if obj_name and obj_name in search_text:
                score = 90
            elif obj_name and search_text in obj_name:
                score = 85
            elif obj_name and obj_name in source_lower:
                score = 80
            elif obj_name and source_lower and any(w in source_lower for w in obj_name.split()):
                score = 60

            # Type keyword match
            keywords = TYPE_KEYWORDS.get(obj_type, [])
            for kw in keywords:
                if kw in search_text or kw in source_lower:
                    score = max(score, 70)
                    break

            # Also try matching object_type directly with underscores removed
            type_readable = obj_type.replace("_", " ")
            if type_readable and type_readable in source_lower:
                score = max(score, 75)

            if score > best_score:
                best_score = score
                best_obj = obj

        if best_obj and best_score >= 60:
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="map_object",
                normalized_ref=f"{best_obj.get('object_type', 'object')}",
                lat=best_obj["lat"],
                lon=best_obj["lon"],
                confidence=best_score / 100.0,
            )

        return None

    def _resolve_snail(self, ref: LocationRefRaw, normalized: str) -> ResolvedLocation:
        """Resolve a snail path like 'B8-2-4' to coordinates."""
        if self.grid_service is None:
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="snail",
                normalized_ref=normalized.upper(),
                confidence=0.3,
            )

        try:
            path = normalized.upper()
            if not self.grid_service.validate_snail(path):
                return ResolvedLocation(
                    source_text=ref.source_text,
                    ref_type="snail",
                    normalized_ref=path,
                    confidence=0.2,
                )

            center = self.grid_service.snail_to_center(path)
            # Count depth: "B8-2-4" → depth 2
            depth = path.count("-")

            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="snail",
                normalized_ref=path,
                lat=center.y,
                lon=center.x,
                confidence=0.95,
                resolution_depth=depth,
            )
        except Exception as e:
            logger.warning("Snail resolution failed for %r: %s", normalized, e)
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="snail",
                normalized_ref=normalized.upper(),
                confidence=0.2,
            )

    def _resolve_grid(self, ref: LocationRefRaw, normalized: str) -> ResolvedLocation:
        """Resolve a grid square like 'B8' to its centroid."""
        if self.grid_service is None:
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="grid",
                normalized_ref=normalized.upper(),
                confidence=0.3,
            )

        try:
            label = normalized.upper()
            if not self.grid_service.validate_square(label):
                return ResolvedLocation(
                    source_text=ref.source_text,
                    ref_type="grid",
                    normalized_ref=label,
                    confidence=0.2,
                )

            poly = self.grid_service.square_to_polygon(label)
            centroid = poly.centroid

            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="grid",
                normalized_ref=label,
                lat=centroid.y,
                lon=centroid.x,
                confidence=0.9,
                resolution_depth=0,
            )
        except Exception as e:
            logger.warning("Grid resolution failed for %r: %s", normalized, e)
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="grid",
                normalized_ref=normalized.upper(),
                confidence=0.2,
            )

    def _resolve_coordinate(self, ref: LocationRefRaw, normalized: str) -> ResolvedLocation:
        """Resolve explicit coordinates like '48.85,2.35'."""
        try:
            parts = re.split(r'[,;\s]+', normalized.strip())
            lat, lon = float(parts[0]), float(parts[1])
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="coordinate",
                normalized_ref=f"{lat},{lon}",
                lat=lat,
                lon=lon,
                confidence=0.95,
            )
        except (ValueError, IndexError):
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="coordinate",
                normalized_ref=normalized,
                confidence=0.1,
            )

    def _resolve_relative(
        self,
        ref: LocationRefRaw,
        normalized: str,
        unit_pos: tuple[float, float] | None,
        heading_deg: float | None,
    ) -> ResolvedLocation:
        """Resolve a relative direction like 'southeast' or 'слева'."""
        if unit_pos is None:
            return ResolvedLocation(
                source_text=ref.source_text,
                ref_type="relative",
                normalized_ref=normalized,
                confidence=0.2,
            )

        # Look up bearing
        key = normalized.lower().strip()
        bearing = DIRECTION_BEARINGS.get(key)

        if bearing is None:
            # Check for left/right which need heading context
            if key in ("left", "слева", "лево", "левее", "flank_left"):
                if heading_deg is not None:
                    bearing = (heading_deg - 90) % 360
                else:
                    bearing = 270  # default: west
            elif key in ("right", "справа", "право", "правее", "flank_right"):
                if heading_deg is not None:
                    bearing = (heading_deg + 90) % 360
                else:
                    bearing = 90  # default: east
            else:
                return ResolvedLocation(
                    source_text=ref.source_text,
                    ref_type="relative",
                    normalized_ref=normalized,
                    confidence=0.2,
                )

        # Offset from unit position
        lat, lon = unit_pos
        bearing_rad = math.radians(bearing)
        d_lat = DEFAULT_RELATIVE_OFFSET_M * math.cos(bearing_rad) / METERS_PER_DEG_LAT
        d_lon = DEFAULT_RELATIVE_OFFSET_M * math.sin(bearing_rad) / METERS_PER_DEG_LON

        target_lat = lat + d_lat
        target_lon = lon + d_lon

        return ResolvedLocation(
            source_text=ref.source_text,
            ref_type="relative",
            normalized_ref=normalized,
            lat=target_lat,
            lon=target_lon,
            confidence=0.6,
        )

    def _resolve_from_source(
        self,
        ref: LocationRefRaw,
        unit_pos: tuple[float, float] | None,
        heading_deg: float | None,
    ) -> ResolvedLocation:
        """Try to auto-detect and resolve from source_text."""
        source = ref.source_text

        # Try snail pattern in source text
        m = re.search(r'([A-Za-z]\d+(?:-\d){1,3})', source)
        if m:
            return self._resolve_snail(ref, m.group(1))

        # Try grid square pattern
        m = re.search(r'\b([A-Za-z])(\d{1,2})\b', source)
        if m:
            return self._resolve_grid(ref, m.group())

        # Try coordinate pattern
        m = re.search(r'(-?\d+\.?\d*)\s*[,;]\s*(-?\d+\.?\d*)', source)
        if m:
            return self._resolve_coordinate(ref, f"{m.group(1)},{m.group(2)}")

        # Try height/elevation reference
        height_m = re.search(
            r'(?:height|hill|elevation|высот[аыеу]|выс\.?|отм\.?)\s*(\d+(?:\.\d+)?)',
            source, re.IGNORECASE
        )
        if height_m:
            return self._resolve_height(ref, f"height {height_m.group(1)}")

        # Try direction keywords in source
        source_lower = source.lower()
        for direction, bearing in DIRECTION_BEARINGS.items():
            if direction in source_lower and len(direction) > 2:
                return self._resolve_relative(ref, direction, unit_pos, heading_deg)

        # Try map object name resolution as last resort
        map_obj_result = self._resolve_map_object(ref, ref.normalized or ref.source_text)
        if map_obj_result and map_obj_result.lat is not None:
            return map_obj_result

        # Unresolvable
        return ResolvedLocation(
            source_text=ref.source_text,
            ref_type="unknown",
            normalized_ref=ref.normalized or ref.source_text,
            confidence=0.1,
        )


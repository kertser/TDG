"""
Detection engine – checks visibility between opposing units.

Uses formulas from AGENTS.MD Section 8.3:
  detection_range_effective = base_detection_range × terrain_visibility × weather_mod
  detection_probability = base_prob × (1 - distance/range) × posture_mod × recon_bonus
  Deterministic hash for reproducibility.

Recon concealment: stationary recon/sniper/observation units in concealment
mode are extremely difficult to detect. They can only be found by chance,
from short range, modified by terrain, weather, and day/night cycle.
"""

from __future__ import annotations

import hashlib
import math
import uuid

from geoalchemy2.shape import to_shape

from backend.engine.terrain import TerrainService

# Approximate meters per degree
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Unit-type-specific eye heights (meters above ground) for LOS checks.
# Must stay in sync with backend/api/units.py UNIT_EYE_HEIGHTS.
UNIT_EYE_HEIGHTS: dict[str, float] = {
    "observation_post":   8.0,    # elevated observation platform / optics on mast
    "tank_company":       3.0,    # turret height
    "tank_platoon":       3.0,
    "mech_company":       2.8,    # IFV turret
    "mech_platoon":       2.8,
    "recon_team":         3.0,    # optics on vehicle or elevated position
    "recon_section":      3.0,
    "sniper_team":        2.5,    # often on elevated positions
    "headquarters":       3.0,    # command vehicle
    "command_post":       3.0,
    "artillery_battery":  2.5,
    "artillery_platoon":  2.5,
}
DEFAULT_EYE_HEIGHT = 2.0

# Unit types that have trained concealment abilities (recon, snipers, observation posts)
CONCEALMENT_UNIT_TYPES = {
    "recon_team", "recon_section", "sniper_team", "observation_post",
    "engineer_recon_team",
}

# Maximum detection range against a concealed recon unit (meters).
# Beyond this, they are effectively invisible. Terrain/weather/night further reduce this.
CONCEALMENT_MAX_RANGE_M = 300.0

# Base detection probability against a concealed unit (very low)
CONCEALMENT_BASE_PROB = 0.10


def _is_concealed(target) -> bool:
    """Check if a unit is in concealment mode.

    A unit is concealed when:
    - It is a concealment-capable type (recon, sniper, observation post)
    - It is NOT actively moving, attacking, or disengaging
    - Its morale is reasonable (above 0.25 — panicked units can't maintain concealment)
    """
    if target.unit_type not in CONCEALMENT_UNIT_TYPES:
        return False

    task = target.current_task
    if task:
        task_type = task.get("type", "")
        # Moving, attacking, or disengaging breaks concealment
        if task_type in ("move", "advance", "attack", "engage", "fire", "disengage"):
            return False

    # Broken morale = can't maintain concealment
    morale = target.morale or 1.0
    if morale < 0.25:
        return False

    return True


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _deterministic_roll(tick: int, observer_id: uuid.UUID, target_id: uuid.UUID) -> float:
    """Deterministic pseudo-random [0,1) for replay reproducibility."""
    h = hashlib.sha256(f"{tick}:{observer_id}:{target_id}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def _posture_modifier(target_task: dict | None) -> float:
    """Target posture affects detection difficulty."""
    if not target_task:
        return 0.6  # stationary
    task_type = target_task.get("type", "")
    if task_type in ("move", "advance", "attack"):
        return 1.0  # moving
    elif task_type == "disengage":
        return 0.5  # breaking contact — trying to stay low while moving
    elif task_type in ("defend", "dig_in"):
        return 0.3  # dug in
    return 0.6  # stationary


def _estimate_unit_type(actual_type: str, distance_m: float, is_recon_observer: bool) -> tuple[str, str | None]:
    """Estimate what an observer can identify about a target based on distance.

    Returns (estimated_type, estimated_size):
    - Close range (<300m, or <500m for recon): full type + size (e.g. "infantry_squad")
    - Medium range (300-800m, or 500-1200m for recon): category + size (e.g. "infantry", "team")
    - Long range (>800m, or >1200m for recon): only category (e.g. "infantry", None)

    Recon/observation units have better optics and training, extending identification range.
    """
    t = (actual_type or "").lower()

    # Determine category
    if "infantry" in t or "mech" in t:
        category = "infantry"
    elif "tank" in t:
        category = "armor"
    elif "artillery" in t or "mortar" in t:
        category = "artillery"
    elif "recon" in t or "sniper" in t or "observation" in t:
        category = "recon"
    elif "engineer" in t or "mine" in t or "breacher" in t or "avlb" in t:
        category = "engineer"
    elif "logistics" in t:
        category = "support"
    elif "command" in t or "headquarters" in t:
        category = "command"
    else:
        category = "unknown"

    # Determine size label
    size = None
    if "battalion" in t:
        size = "battalion"
    elif "company" in t or "battery" in t:
        size = "company"
    elif "platoon" in t:
        size = "platoon"
    elif "section" in t:
        size = "section"
    elif "squad" in t:
        size = "squad"
    elif "team" in t:
        size = "team"

    # Distance thresholds (recon observers have better optics)
    close_range = 500.0 if is_recon_observer else 300.0
    medium_range = 1200.0 if is_recon_observer else 800.0

    if distance_m <= close_range:
        # Close range: full identification
        return actual_type, size
    elif distance_m <= medium_range:
        # Medium range: category + approximate size
        return category, size
    else:
        # Long range: only general category, no size
        return category, None


def _is_in_smoke(lat: float, lon: float, map_objects: list | None) -> bool:
    """Check if a point is inside an active visibility-reducing effect (smoke, fog, chemical cloud)."""
    if not map_objects:
        return False
    VISIBILITY_EFFECT_TYPES = {"smoke", "fog_effect", "fire_effect", "chemical_cloud"}
    for obj in map_objects:
        if obj.object_type not in VISIBILITY_EFFECT_TYPES or not obj.is_active:
            continue
        if obj.geometry is None:
            continue
        try:
            from shapely.geometry import Point as ShapelyPoint
            shape = to_shape(obj.geometry)
            pt = ShapelyPoint(lon, lat)
            if shape.contains(pt) or shape.distance(pt) * 111320 < 10:
                return True
        except Exception:
            continue
    return False


def process_detection(
    blue_units: list,
    red_units: list,
    tick: int,
    terrain: TerrainService,
    weather_visibility_mod: float = 1.0,
    existing_contacts: list | None = None,
    los_service=None,
    map_objects: list | None = None,
    night_mod: float = 1.0,
) -> list[dict]:
    """
    Run detection checks between opposing sides.

    Returns list of new/updated contact dicts:
      {observing_side, observing_unit_id, target_unit, estimated_type,
       estimated_size, location_estimate, location_accuracy_m, confidence, source}

    If los_service is provided, uses LOS checks to verify that terrain
    doesn't block the line of sight between observer and target.
    """
    from backend.services.debug_logger import dlog, is_debug_logging_enabled
    _debug = is_debug_logging_enabled()

    new_contacts = []

    if _debug:
        dlog(f"    [detection] blue_units={len(blue_units)} red_units={len(red_units)} weather_vis={weather_visibility_mod:.2f} night={night_mod:.2f}")

    # Run both directions
    for observers, targets, obs_side in [
        (blue_units, red_units, "blue"),
        (red_units, blue_units, "red"),
    ]:
        for observer in observers:
            if observer.is_destroyed or observer.position is None:
                continue

            try:
                obs_pt = to_shape(observer.position)
                obs_lon, obs_lat = obs_pt.x, obs_pt.y
            except Exception:
                continue

            base_range = observer.detection_range_m or 1500.0

            # Observer capabilities (used for NVG and recon checks)
            capabilities = observer.capabilities or {}

            # Height advantage detection bonus
            height_bonus = 1.0

            # Night vision: units with NVG get reduced night penalty
            obs_night_mod = 1.0
            if night_mod < 1.0:
                has_nvg = capabilities.get("has_nvg", False) or capabilities.get("night_vision", False)
                if has_nvg:
                    # NVG reduces night penalty by ~50%
                    obs_night_mod = 1.0 - (1.0 - night_mod) * 0.5
                else:
                    obs_night_mod = night_mod

            # Detection RANGE: base × weather × night (NOT terrain — terrain affects probability)
            # This keeps range consistent with the fog-of-war visibility service which uses
            # base_range directly. Terrain visibility affects detection PROBABILITY instead,
            # making it harder to spot enemies in dense terrain without artificially reducing range.
            effective_range = base_range * weather_visibility_mod * obs_night_mod

            # Observer eye height depends on unit type (OP = 8m, tanks = 3m, infantry = 2m)
            observer_eye_h = UNIT_EYE_HEIGHTS.get(observer.unit_type, DEFAULT_EYE_HEIGHT)

            # Recon bonus
            is_recon = capabilities.get("is_recon", False)
            base_prob = 0.8 if is_recon else 0.6
            recon_bonus = 1.3 if is_recon else 1.0

            for target in targets:
                if target.is_destroyed or target.position is None:
                    continue

                try:
                    tgt_pt = to_shape(target.position)
                    tgt_lon, tgt_lat = tgt_pt.x, tgt_pt.y
                except Exception:
                    continue

                # ── Check if target is a concealed recon unit ──
                target_concealed = _is_concealed(target)

                if target_concealed:
                    # Concealed recon: severely limited detection range
                    # Base max range modified by terrain, weather, and night
                    target_terrain_vis = terrain.visibility_factor(tgt_lon, tgt_lat)
                    concealment_range = (
                        CONCEALMENT_MAX_RANGE_M
                        * target_terrain_vis      # forest=0.4 → 120m, open=1.0 → 300m
                        * weather_visibility_mod   # rain → even shorter
                        * obs_night_mod            # night → very short
                    )

                    dist = _distance_m(obs_lat, obs_lon, tgt_lat, tgt_lon)
                    if dist > concealment_range:
                        continue  # too far to detect a concealed unit

                    # LOS check
                    if los_service is not None:
                        if not los_service.has_los(obs_lon, obs_lat, tgt_lon, tgt_lat,
                                                   eye_height=observer_eye_h):
                            continue

                    # Very low probability: distance factor, terrain, weather, morale
                    distance_factor = max(0.0, 1.0 - dist / concealment_range)
                    # Recon observers are slightly better at finding hidden units
                    observer_skill = 1.3 if is_recon else 1.0

                    prob = (
                        CONCEALMENT_BASE_PROB
                        * distance_factor
                        * target_terrain_vis   # harder in dense terrain (counterintuitive: visible terrain = easier to spot)
                        * observer_skill
                    )
                    # Smoke still applies
                    if _is_in_smoke(tgt_lat, tgt_lon, map_objects):
                        prob *= 0.05
                    prob = min(prob, 0.25)  # cap at 25% — concealed units are HARD to find

                    roll = _deterministic_roll(tick, observer.id, target.id)
                    if roll < prob:
                        accuracy = max(80.0, dist * 0.1 + (1.0 - prob) * 300.0)
                        est_type, est_size = _estimate_unit_type(target.unit_type, dist, is_recon)
                        new_contacts.append({
                            "observing_side": obs_side,
                            "observing_unit_id": observer.id,
                            "target_unit_id": target.id,
                            "estimated_type": est_type,
                            "estimated_size": est_size,
                            "lat": tgt_lat,
                            "lon": tgt_lon,
                            "location_accuracy_m": accuracy,
                            "confidence": prob,
                            "source": "recon" if is_recon else "visual",
                        })
                    continue  # skip normal detection logic for concealed units

                # ── Normal (non-concealed) detection logic ──

                # Apply height advantage to effective range for this pair
                pair_height_bonus = terrain.detection_height_bonus(
                    obs_lon, obs_lat, tgt_lon, tgt_lat
                )
                pair_effective_range = effective_range * pair_height_bonus

                dist = _distance_m(obs_lat, obs_lon, tgt_lat, tgt_lon)
                if dist > pair_effective_range:
                    if _debug:
                        dlog(f"      [det-skip] {obs_side} {observer.name}→{target.name}: dist={dist:.0f}m > range={pair_effective_range:.0f}m (base={base_range:.0f} h_bonus={pair_height_bonus:.2f})")
                    continue

                # LOS check: verify terrain doesn't block line of sight
                if los_service is not None:
                    if not los_service.has_los(obs_lon, obs_lat, tgt_lon, tgt_lat,
                                               eye_height=observer_eye_h):
                        if _debug:
                            dlog(f"      [det-skip] {obs_side} {observer.name}→{target.name}: LOS blocked (dist={dist:.0f}m)")
                        continue  # LOS blocked by terrain

                # Detection probability
                posture_mod = _posture_modifier(target.current_task)

                # Quadratic distance falloff: 1 - (d/r)²
                # Gives much better detection at medium ranges than linear (1 - d/r).
                # At 50% range: 0.75 (vs 0.50 linear), at 80% range: 0.36 (vs 0.20).
                # This prevents the unrealistic scenario where units walk within 800m
                # of each other without detecting anything.
                ratio = dist / pair_effective_range
                distance_factor = max(0.0, 1.0 - ratio * ratio)

                # Target terrain concealment: targets in obscuring terrain
                # (forest, urban, scrub) are harder to detect even if LOS exists.
                # Maps terrain visibility factor 0.4–1.0 → concealment 0.7–1.0
                # Note: observer terrain is NOT penalized here — LOS checks already
                # handle terrain obstruction, and double-penalizing would make
                # detection unrealistically difficult.
                target_terrain_vis = terrain.visibility_factor(tgt_lon, tgt_lat)
                target_concealment = 0.5 + 0.5 * target_terrain_vis

                # Smoke modifier: target or observer in smoke → near-zero detection
                smoke_mod = 1.0
                if _is_in_smoke(tgt_lat, tgt_lon, map_objects):
                    smoke_mod *= 0.1
                if _is_in_smoke(obs_lat, obs_lon, map_objects):
                    smoke_mod *= 0.15

                prob = base_prob * distance_factor * posture_mod * recon_bonus * target_concealment * smoke_mod
                prob = min(prob, 0.95)  # cap at 95%

                roll = _deterministic_roll(tick, observer.id, target.id)

                if _debug:
                    dlog(f"      [det-check] {obs_side} {observer.name}→{target.name}: dist={dist:.0f}m range={pair_effective_range:.0f}m prob={prob:.3f} roll={roll:.3f} {'HIT' if roll < prob else 'MISS'}")

                if roll < prob:
                    # Detection successful!
                    # Add some inaccuracy to position estimate
                    accuracy = max(50.0, dist * 0.05 + (1.0 - prob) * 200.0)
                    # Estimate unit type based on distance and observer capabilities
                    est_type, est_size = _estimate_unit_type(target.unit_type, dist, is_recon)

                    new_contacts.append({
                        "observing_side": obs_side,
                        "observing_unit_id": observer.id,
                        "target_unit_id": target.id,
                        "estimated_type": est_type,
                        "estimated_size": est_size,
                        "lat": tgt_lat,
                        "lon": tgt_lon,
                        "location_accuracy_m": accuracy,
                        "confidence": prob,
                        "source": "recon" if is_recon else "visual",
                    })

    if _debug and new_contacts:
        dlog(f"    [detection] Total new_contacts: {len(new_contacts)}")
        for nc in new_contacts[:5]:
            dlog(f"      contact: {nc['observing_side']} observer={nc['observing_unit_id']} → target_type={nc.get('estimated_type')}")
    return new_contacts



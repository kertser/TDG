"""
Detection engine – checks visibility between opposing units.

Uses formulas from AGENTS.MD Section 8.3:
  detection_range_effective = base_detection_range × terrain_visibility × weather_mod
  detection_probability = base_prob × (1 - distance/range) × posture_mod × recon_bonus
  Deterministic hash for reproducibility.
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
    elif task_type in ("defend", "dig_in"):
        return 0.3  # dug in
    return 0.6  # stationary


def process_detection(
    blue_units: list,
    red_units: list,
    tick: int,
    terrain: TerrainService,
    weather_visibility_mod: float = 1.0,
    existing_contacts: list | None = None,
) -> list[dict]:
    """
    Run detection checks between opposing sides.

    Returns list of new/updated contact dicts:
      {observing_side, observing_unit_id, target_unit, estimated_type,
       estimated_size, location_estimate, location_accuracy_m, confidence, source}
    """
    new_contacts = []

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
            terrain_vis = terrain.visibility_factor(obs_lon, obs_lat)
            effective_range = base_range * terrain_vis * weather_visibility_mod

            # Recon bonus
            capabilities = observer.capabilities or {}
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

                dist = _distance_m(obs_lat, obs_lon, tgt_lat, tgt_lon)
                if dist > effective_range:
                    continue

                # Detection probability
                posture_mod = _posture_modifier(target.current_task)
                distance_factor = max(0.0, 1.0 - dist / effective_range)
                prob = base_prob * distance_factor * posture_mod * recon_bonus
                prob = min(prob, 0.95)  # cap at 95%

                roll = _deterministic_roll(tick, observer.id, target.id)

                if roll < prob:
                    # Detection successful!
                    # Add some inaccuracy to position estimate
                    accuracy = max(50.0, dist * 0.05 + (1.0 - prob) * 200.0)

                    new_contacts.append({
                        "observing_side": obs_side,
                        "observing_unit_id": observer.id,
                        "target_unit_id": target.id,
                        "estimated_type": target.unit_type,
                        "estimated_size": None,
                        "lat": tgt_lat,
                        "lon": tgt_lon,
                        "location_accuracy_m": accuracy,
                        "confidence": prob,
                        "source": "recon" if is_recon else "visual",
                    })

    return new_contacts



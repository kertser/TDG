"""
Deterministic pseudo-random number generation for the simulation engine.

All engine modules that need reproducible randomness must use `deterministic_roll`
so that session replay produces identical results given the same seed sequence.
"""

from __future__ import annotations

import hashlib


def deterministic_roll(tick: int, *ids) -> float:
    """
    Stable pseudo-random float in [0, 1) derived from tick + arbitrary ids.

    Inputs are hashed with BLAKE2b — fast, collision-resistant, reproducible
    across Python versions.  Same inputs always produce the same output,
    enabling deterministic replay.

    Usage examples:
        roll = deterministic_roll(tick, unit.id)                # 0..1
        angle = deterministic_roll(tick, unit.id, "angle") * 360
        success = deterministic_roll(tick, unit.id, target.id) < probability
    """
    raw = f"{tick}:" + ":".join(str(i) for i in ids)
    h = hashlib.blake2b(raw.encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64


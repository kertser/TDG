"""
Red AI Doctrine Profiles — maps risk posture to behavioral parameters
and prompt instructions.

Four postures: aggressive, balanced, cautious, defensive.
Each profile defines decision thresholds used by both the rule-based
fallback and the LLM prompt construction.
"""

from __future__ import annotations


DOCTRINE_PROFILES: dict[str, dict] = {
    "aggressive": {
        "description": "Seeks decisive engagement. Pursues the enemy relentlessly.",
        "engage_distance_factor": 1.3,    # Engages at longer range
        "retreat_threshold": 0.15,         # Only retreats at very low strength
        "advance_bias": 0.8,              # Strong bias to advance toward enemy
        "counter_attack_threshold": 0.4,  # Counter-attacks even at moderate strength
        "patrol_range_factor": 1.5,       # Patrols wider area
        "hold_position_bias": 0.1,        # Rarely holds — prefers to attack
        "pursuit_aggression": 0.9,        # Aggressively pursues retreating enemy
        "risk_tolerance": 0.8,            # Accepts high risk
        "prompt_instruction": (
            "You are an AGGRESSIVE commander. You seek decisive engagement at every opportunity. "
            "Prioritize attacking and destroying enemy forces. Accept risk to gain advantage. "
            "Pursue retreating enemies. Only withdraw if facing total destruction. "
            "Concentrate forces for overwhelming attacks."
        ),
    },
    "balanced": {
        "description": "Prudent aggression with risk management. Attacks when favorable.",
        "engage_distance_factor": 1.0,
        "retreat_threshold": 0.30,
        "advance_bias": 0.5,
        "counter_attack_threshold": 0.55,
        "patrol_range_factor": 1.0,
        "hold_position_bias": 0.4,
        "pursuit_aggression": 0.5,
        "risk_tolerance": 0.5,
        "prompt_instruction": (
            "You are a BALANCED commander. You attack when conditions are favorable and "
            "defend when they are not. Manage risk carefully. Maintain reserves. "
            "Coordinate attacks with supporting units. Withdraw if situation becomes unfavorable. "
            "Seek opportunities to flank or surprise the enemy."
        ),
    },
    "cautious": {
        "description": "Prioritizes force preservation. Engages only with advantage.",
        "engage_distance_factor": 0.8,
        "retreat_threshold": 0.45,
        "advance_bias": 0.2,
        "counter_attack_threshold": 0.7,
        "patrol_range_factor": 0.7,
        "hold_position_bias": 0.7,
        "pursuit_aggression": 0.2,
        "risk_tolerance": 0.3,
        "prompt_instruction": (
            "You are a CAUTIOUS commander. Prioritize force preservation above all. "
            "Only engage when you have clear numerical or positional advantage. "
            "Use terrain for cover and concealment. Pull back early if threatened. "
            "Avoid unnecessary risks. Maintain defensive positions."
        ),
    },
    "defensive": {
        "description": "Holds ground tenaciously. Rarely advances unless forced.",
        "engage_distance_factor": 0.7,
        "retreat_threshold": 0.20,          # Holds even when hurt
        "advance_bias": 0.05,
        "counter_attack_threshold": 0.85,   # Only counter-attacks if very strong
        "patrol_range_factor": 0.5,
        "hold_position_bias": 0.95,
        "pursuit_aggression": 0.1,
        "risk_tolerance": 0.2,
        "prompt_instruction": (
            "You are a DEFENSIVE commander. Hold your assigned positions at all costs. "
            "Do NOT advance unless directly ordered. Dig in and use terrain. "
            "Concentrate fire on approaching enemies. Only withdraw if position becomes "
            "completely untenable. Do not pursue retreating enemies beyond your sector."
        ),
    },
}


def get_doctrine(posture: str) -> dict:
    """Get doctrine profile by posture name. Defaults to 'balanced'."""
    return DOCTRINE_PROFILES.get(posture, DOCTRINE_PROFILES["balanced"])


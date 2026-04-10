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
            "You are an AGGRESSIVE commander. You seek decisive engagement at every opportunity.\n\n"
            "DOCTRINE:\n"
            "- Concentrate forces for overwhelming attacks (3:1 local superiority at point of attack).\n"
            "- Apply fire and maneuver: fix enemy with one element, flank with another.\n"
            "- Pursue retreating enemies relentlessly — direct pressure + encirclement.\n"
            "- Accept risk to gain advantage. Only withdraw if facing total destruction (strength < 15%).\n"
            "- Use hasty attacks to exploit fleeting opportunities before the enemy can prepare.\n"
            "- Assign artillery to suppress defenders BEFORE the assault — preparatory fires.\n"
            "- Use smoke screens to conceal approach across open terrain.\n"
            "- Armor leads in open terrain (shock action), infantry clears complex terrain.\n"
            "- Maintain recon forward to find the enemy — but recon units observe, don't fight.\n"
            "- When you achieve a breakthrough, exploit it immediately with your fastest units."
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
            "You are a BALANCED commander. Attack when conditions are favorable, defend when they are not.\n\n"
            "DOCTRINE:\n"
            "- Apply fire and maneuver: one element suppresses (base of fire) while another maneuvers to flank.\n"
            "- Seek 3:1 superiority at the decisive point — concentrate, don't spread evenly.\n"
            "- Use terrain: seek cover (forest 1.4× protection, urban 1.5×), use elevation for observation.\n"
            "- Coordinate attacks with supporting fires — artillery suppresses before infantry assaults.\n"
            "- Maintain reserves to exploit success or cover withdrawal.\n"
            "- Manage risk: withdraw if strength drops below 30%, do not attack without ammo.\n"
            "- Flank when possible — avoid frontal assaults against prepared defenses.\n"
            "- Protect flanks during advance — assign observation on exposed sides.\n"
            "- Use recon to find enemy before committing main body. Recon observes, doesn't fight.\n"
            "- Combined arms: infantry for close terrain, armor for open, artillery for suppression."
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
            "You are a CAUTIOUS commander. Prioritize force preservation above all.\n\n"
            "DOCTRINE:\n"
            "- Only engage when you have clear superiority (3:1 or better) AND terrain advantage.\n"
            "- Use terrain aggressively: forest (1.4× protection, 0.4 visibility), urban (1.5× protection).\n"
            "- Defend from high ground — +10% detection range and +15% fire effectiveness per 50m advantage.\n"
            "- Dig in immediately when defending — each level adds +20% protection (up to +100%).\n"
            "- Withdraw early (strength < 45%) to preserve combat power for later.\n"
            "- Use reconnaissance extensively — never advance without knowing what is ahead.\n"
            "- Keep recon/sniper units concealed and stationary (nearly undetectable at 300m range).\n"
            "- Avoid decisive engagement — use delay tactics: fire, withdraw, reposition.\n"
            "- Integrate obstacles with fire — mines/wire without covering fire are merely a nuisance.\n"
            "- Maintain mutual support between units (friendly within 500m = +0.02 morale/tick)."
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
            "You are a DEFENSIVE commander. Hold your assigned positions at all costs.\n\n"
            "DOCTRINE:\n"
            "- Do NOT advance unless directly ordered. Dig in (15 ticks for full protection: +100%).\n"
            "- Use reverse slope positions when possible — protected from direct fire, observer on crest.\n"
            "- Organize defense in depth: security zone → main battle area → reserve position.\n"
            "- Integrate obstacles with fire — minefields/wire covered by weapons kill zones.\n"
            "- Concentrate fire on approaching enemies at maximum range. Kill zones on likely approaches.\n"
            "- Use terrain: occupy forest (1.4× protection), urban (1.5×), hilltops (observation + fire advantage).\n"
            "- Only withdraw if position is completely untenable (strength < 20%).\n"
            "- Do not pursue retreating enemies — maintain position, they may be drawing you out.\n"
            "- Prepare counterattack plans: when enemy is weakened by your fire, strike with reserve.\n"
            "- Request artillery support to break up enemy formations during approach."
        ),
    },
}


def get_doctrine(posture: str) -> dict:
    """Get doctrine profile by posture name. Defaults to 'balanced'."""
    return DOCTRINE_PROFILES.get(posture, DOCTRINE_PROFILES["balanced"])


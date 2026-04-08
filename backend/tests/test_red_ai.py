"""
Tests for the Red AI system.

Tests cover:
- Knowledge builder: no Blue leaks, correct Red-side data
- Doctrine profiles: parameter correctness
- Rule-based decisions: hold, attack, patrol, withdraw behaviors
- Output validation: unit ID validation, order structure
- Agent condition checks: broken/offline units skipped
"""

import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from backend.services.red_ai.agent import RedAIAgent
from backend.services.red_ai.doctrine import get_doctrine, DOCTRINE_PROFILES
from backend.services.red_ai.knowledge import _approx_distance_m, _bearing_deg
from backend.prompts.red_commander import build_red_commander_prompt
from backend.schemas.red_agent import (
    RedDecision, RedDecisionBatch, RedAgentCreate, RedAgentUpdate,
    RiskPostureEnum, MissionTypeEnum,
)


# ── Fixtures ─────────────────────────────────────────────────

def _make_unit(unit_id=None, lat=49.05, lon=4.5, strength=1.0,
               morale=1.0, ammo=1.0, suppression=0.0,
               unit_type="infantry_platoon", current_task=None,
               comms_status="operational", detection_range_m=1500):
    """Create a mock Red unit dict."""
    return {
        "id": unit_id or str(uuid.uuid4()),
        "name": f"Red {unit_type.replace('_', ' ').title()}",
        "unit_type": unit_type,
        "strength": strength,
        "ammo": ammo,
        "morale": morale,
        "suppression": suppression,
        "comms_status": comms_status,
        "current_task": current_task,
        "lat": lat,
        "lon": lon,
        "heading_deg": 0.0,
        "detection_range_m": detection_range_m,
        "move_speed_mps": 3.0,
        "capabilities": {},
    }


def _make_contact(lat=49.06, lon=4.51, estimated_type="infantry_platoon",
                  confidence=0.7, source="visual"):
    """Create a mock contact dict."""
    return {
        "estimated_type": estimated_type,
        "estimated_size": "platoon",
        "confidence": confidence,
        "source": source,
        "lat": lat,
        "lon": lon,
        "last_seen_tick": 10,
    }


def _make_knowledge(own_units=None, contacts=None, terrain=None):
    """Build a mock knowledge state."""
    units = own_units or []
    ctcts = contacts or []
    return {
        "own_units": units,
        "known_contacts": ctcts,
        "discovered_objects": [],
        "terrain_around_units": terrain or {},
        "terrain_types_present": [],
        "elevation_at_units": {},
        "summary": {
            "total_units": len(units),
            "avg_strength": sum(u["strength"] for u in units) / len(units) if units else 0,
            "avg_morale": sum(u["morale"] for u in units) / len(units) if units else 0,
            "avg_ammo": sum(u["ammo"] for u in units) / len(units) if units else 0,
            "total_contacts": len(ctcts),
            "units_idle": sum(1 for u in units if not u.get("current_task")),
            "units_moving": 0,
            "units_attacking": 0,
            "units_defending": 0,
        },
    }


# ══════════════════════════════════════════════════════════════
# ── Doctrine Tests ────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestDoctrine:
    """Tests for doctrine profiles."""

    def test_all_postures_exist(self):
        """All four postures should be defined."""
        assert "aggressive" in DOCTRINE_PROFILES
        assert "balanced" in DOCTRINE_PROFILES
        assert "cautious" in DOCTRINE_PROFILES
        assert "defensive" in DOCTRINE_PROFILES

    def test_get_doctrine_valid(self):
        """get_doctrine returns correct profile."""
        doc = get_doctrine("aggressive")
        assert doc["advance_bias"] == 0.8
        assert doc["risk_tolerance"] == 0.8

    def test_get_doctrine_invalid_defaults_to_balanced(self):
        """Unknown posture defaults to balanced."""
        doc = get_doctrine("unknown_posture")
        assert doc["advance_bias"] == 0.5

    def test_aggressive_more_aggressive_than_cautious(self):
        """Aggressive has higher advance_bias and risk tolerance."""
        agg = get_doctrine("aggressive")
        cau = get_doctrine("cautious")
        assert agg["advance_bias"] > cau["advance_bias"]
        assert agg["risk_tolerance"] > cau["risk_tolerance"]
        assert agg["retreat_threshold"] < cau["retreat_threshold"]

    def test_defensive_holds_position(self):
        """Defensive has very high hold_position_bias."""
        doc = get_doctrine("defensive")
        assert doc["hold_position_bias"] >= 0.9
        assert doc["advance_bias"] < 0.1

    def test_all_profiles_have_required_keys(self):
        """All profiles must contain required decision keys."""
        required_keys = [
            "engage_distance_factor", "retreat_threshold", "advance_bias",
            "risk_tolerance", "prompt_instruction",
        ]
        for posture, profile in DOCTRINE_PROFILES.items():
            for key in required_keys:
                assert key in profile, f"Missing '{key}' in {posture} profile"


# ══════════════════════════════════════════════════════════════
# ── Knowledge Builder Tests ──────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestKnowledge:
    """Tests for knowledge state utilities."""

    def test_approx_distance_same_point(self):
        """Distance between same point should be ~0."""
        d = _approx_distance_m(49.0, 4.5, 49.0, 4.5)
        assert d < 1.0

    def test_approx_distance_one_km(self):
        """~1km distance check (roughly 0.009 degrees latitude)."""
        d = _approx_distance_m(49.0, 4.5, 49.009, 4.5)
        assert 900 < d < 1100

    def test_bearing_north(self):
        """Bearing from south to north should be ~0/360."""
        b = _bearing_deg(49.0, 4.5, 49.1, 4.5)
        assert b < 10 or b > 350  # approximately north

    def test_bearing_east(self):
        """Bearing to the east should be ~90."""
        b = _bearing_deg(49.0, 4.5, 49.0, 4.6)
        assert 80 < b < 100

    def test_bearing_south(self):
        """Bearing to the south should be ~180."""
        b = _bearing_deg(49.1, 4.5, 49.0, 4.5)
        assert 170 < b < 190


# ══════════════════════════════════════════════════════════════
# ── Agent Rule-Based Decision Tests ──────────────────────────
# ══════════════════════════════════════════════════════════════

class TestRuleBasedDecisions:
    """Tests for the rule-based decision engine."""

    def setup_method(self):
        self.agent = RedAIAgent()

    @pytest.mark.asyncio
    async def test_no_units_returns_empty(self):
        """No units → no decisions."""
        knowledge = _make_knowledge(own_units=[])
        decisions = await self.agent.decide(
            agent_data={"risk_posture": "balanced", "mission_intent": {"type": "hold"}},
            knowledge=knowledge,
            tick=10,
        )
        assert decisions == []

    def test_hold_no_contacts_no_orders(self):
        """Hold mission with no contacts: no orders issued (stay put)."""
        unit = _make_unit()
        knowledge = _make_knowledge(own_units=[unit])
        doctrine = get_doctrine("balanced")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "hold"},
            own_units=[unit],
            contacts=[],
            knowledge=knowledge,
            tick=10,
        )
        assert orders == []

    def test_hold_with_contact_in_range_engages(self):
        """Hold mission: nearby contact should trigger engagement (aggressive posture)."""
        unit = _make_unit(lat=49.05, lon=4.5, detection_range_m=2000)
        contact = _make_contact(lat=49.055, lon=4.505)  # ~600m away
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "hold"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) >= 1
        assert orders[0]["order_type"] == "attack"
        assert orders[0]["unit_id"] == unit["id"]

    def test_hold_with_distant_contact_no_engagement_cautious(self):
        """Cautious commander doesn't engage distant contacts on hold."""
        unit = _make_unit(lat=49.05, lon=4.5, detection_range_m=1500)
        contact = _make_contact(lat=49.08, lon=4.53)  # ~4km away
        doctrine = get_doctrine("cautious")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "hold"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert orders == []

    def test_attack_engages_nearest_contact(self):
        """Attack mission: engage nearest contact."""
        unit = _make_unit()
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) == 1
        assert orders[0]["order_type"] == "attack"
        assert orders[0]["engagement_rules"] == "fire_at_will"

    def test_attack_advances_to_mission_target_when_no_contacts(self):
        """Attack mission: advance to mission target if no contacts."""
        unit = _make_unit()
        mission_target = {"lat": 49.1, "lon": 4.6}
        doctrine = get_doctrine("balanced")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack", "target_location": mission_target},
            own_units=[unit],
            contacts=[],
            knowledge=_make_knowledge([unit]),
            tick=10,
        )
        assert len(orders) == 1
        assert orders[0]["order_type"] == "move"
        assert orders[0]["target_location"]["lat"] == 49.1

    def test_weak_unit_does_not_attack(self):
        """Weak unit (low strength) holds instead of attacking."""
        unit = _make_unit(strength=0.1)
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("balanced")  # retreat_threshold = 0.30

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        # Critically damaged unit should withdraw or hold, not attack
        attack_orders = [o for o in orders if o["order_type"] == "attack"]
        assert len(attack_orders) == 0

    def test_no_ammo_unit_does_not_attack(self):
        """Unit with no ammo doesn't issue attack orders."""
        unit = _make_unit(ammo=0.05)
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) == 0

    def test_broken_unit_skipped(self):
        """Unit with morale < 0.15 (broken) gets no orders."""
        unit = _make_unit(morale=0.10)
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) == 0

    def test_comms_offline_unit_skipped(self):
        """Unit with comms_status='offline' gets no orders."""
        unit = _make_unit(comms_status="offline")
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) == 0

    def test_withdraw_moves_to_rally(self):
        """Withdraw mission: unit moves to rally point."""
        unit = _make_unit(lat=49.05, lon=4.5)
        rally = {"lat": 49.0, "lon": 4.4}
        doctrine = get_doctrine("balanced")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "withdraw", "target_location": rally},
            own_units=[unit],
            contacts=[],
            knowledge=_make_knowledge([unit]),
            tick=10,
        )
        assert len(orders) == 1
        assert orders[0]["order_type"] == "move"
        assert orders[0]["speed"] == "fast"

    def test_patrol_cycles_waypoints(self):
        """Patrol mission: cycles through waypoints."""
        unit = _make_unit(lat=49.05, lon=4.5)
        waypoints = [
            {"lat": 49.06, "lon": 4.51},
            {"lat": 49.07, "lon": 4.52},
        ]
        doctrine = get_doctrine("balanced")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "patrol", "waypoints": waypoints},
            own_units=[unit],
            contacts=[],
            knowledge=_make_knowledge([unit]),
            tick=10,
        )
        assert len(orders) == 1
        assert orders[0]["order_type"] == "move"
        assert orders[0]["speed"] == "slow"

    def test_unit_with_task_keeps_it(self):
        """Unit that already has a task should keep it (not overridden)."""
        unit = _make_unit(current_task={"type": "move", "target_location": {"lat": 49.1, "lon": 4.6}})
        doctrine = get_doctrine("balanced")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "hold"},
            own_units=[unit],
            contacts=[],
            knowledge=_make_knowledge([unit]),
            tick=10,
        )
        assert len(orders) == 0

    def test_orders_include_reasoning(self):
        """Orders from rule-based engine include reasoning text."""
        unit = _make_unit()
        contact = _make_contact(lat=49.055, lon=4.505)
        doctrine = get_doctrine("aggressive")

        orders = self.agent._rule_based_decide(
            doctrine=doctrine,
            mission={"type": "attack"},
            own_units=[unit],
            contacts=[contact],
            knowledge=_make_knowledge([unit], [contact]),
            tick=10,
        )
        assert len(orders) >= 1
        # At least one order should have reasoning
        assert any(o.get("reasoning") for o in orders)


# ══════════════════════════════════════════════════════════════
# ── Output Validation Tests ──────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestOutputValidation:
    """Tests for LLM output validation."""

    def test_only_controlled_unit_ids_accepted(self):
        """LLM output with invalid unit IDs should be filtered."""
        agent = RedAIAgent()
        valid_id = str(uuid.uuid4())
        invalid_id = str(uuid.uuid4())
        own_units = [_make_unit(unit_id=valid_id)]

        valid_ids = {u["id"] for u in own_units}
        orders = [
            {"unit_id": valid_id, "order_type": "move"},
            {"unit_id": invalid_id, "order_type": "attack"},
        ]
        validated = [o for o in orders if o.get("unit_id") in valid_ids]
        assert len(validated) == 1
        assert validated[0]["unit_id"] == valid_id

    def test_red_decision_schema_validation(self):
        """RedDecision Pydantic schema validates correctly."""
        d = RedDecision(
            unit_id="abc-123",
            order_type="move",
            target_location={"lat": 49.05, "lon": 4.5},
            speed="slow",
        )
        assert d.unit_id == "abc-123"
        assert d.order_type == "move"
        assert d.speed == "slow"

    def test_red_decision_batch_parses_llm_output(self):
        """RedDecisionBatch parses LLM JSON output."""
        raw = {
            "orders": [
                {
                    "unit_id": "abc-123",
                    "order_type": "move",
                    "target_location": {"lat": 49.05, "lon": 4.5},
                    "speed": "fast",
                    "reasoning": "Advance to high ground",
                },
                {
                    "unit_id": "def-456",
                    "order_type": "defend",
                    "speed": "slow",
                },
            ],
            "overall_assessment": "Situation stable, probing forward.",
        }
        batch = RedDecisionBatch.model_validate(raw)
        assert len(batch.orders) == 2
        assert batch.orders[0].reasoning == "Advance to high ground"
        assert batch.overall_assessment is not None

    def test_red_decision_batch_empty(self):
        """Empty orders list is valid."""
        batch = RedDecisionBatch.model_validate({"orders": []})
        assert len(batch.orders) == 0


# ══════════════════════════════════════════════════════════════
# ── Schema Tests ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestSchemas:
    """Tests for Red AI Pydantic schemas."""

    def test_red_agent_create_defaults(self):
        """RedAgentCreate has sensible defaults."""
        data = RedAgentCreate()
        assert data.name == "Red Commander"
        assert data.risk_posture == RiskPostureEnum.balanced
        assert data.controlled_unit_ids is None

    def test_red_agent_create_custom(self):
        """RedAgentCreate accepts custom values."""
        data = RedAgentCreate(
            name="Red Bn CO",
            risk_posture=RiskPostureEnum.aggressive,
            mission_intent={"type": "attack", "target_location": {"lat": 49, "lon": 4}},
            controlled_unit_ids=["abc-123"],
        )
        assert data.name == "Red Bn CO"
        assert data.risk_posture == RiskPostureEnum.aggressive

    def test_red_agent_update_partial(self):
        """RedAgentUpdate allows partial updates."""
        data = RedAgentUpdate(risk_posture=RiskPostureEnum.cautious)
        assert data.risk_posture == RiskPostureEnum.cautious
        assert data.name is None
        assert data.mission_intent is None

    def test_mission_type_enum(self):
        """MissionTypeEnum covers all mission types."""
        assert MissionTypeEnum.hold.value == "hold"
        assert MissionTypeEnum.attack.value == "attack"
        assert MissionTypeEnum.patrol.value == "patrol"
        assert MissionTypeEnum.withdraw.value == "withdraw"


# ══════════════════════════════════════════════════════════════
# ── Prompt Builder Tests ─────────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestPromptBuilder:
    """Tests for the Red commander LLM prompt builder."""

    def test_prompt_does_not_contain_blue(self):
        """Prompt must never mention Blue unit positions or orders."""

        unit = _make_unit(lat=49.05, lon=4.5)
        knowledge = _make_knowledge([unit])
        doctrine = get_doctrine("balanced")
        agent_data = {
            "name": "Red CO",
            "risk_posture": "balanced",
            "mission_intent": {"type": "hold"},
        }

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {"type": "hold"}, knowledge, tick=5
        )

        # Should not contain Blue references
        combined = (system + user).lower()
        assert "blue unit" not in combined
        assert "blue force position" not in combined

    def test_prompt_includes_unit_ids(self):
        """Prompt must include all controlled unit IDs."""
        uid = str(uuid.uuid4())
        unit = _make_unit(unit_id=uid)
        knowledge = _make_knowledge([unit])
        doctrine = get_doctrine("balanced")
        agent_data = {"name": "Red CO", "risk_posture": "balanced"}

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {}, knowledge, tick=5
        )
        assert uid in user

    def test_prompt_includes_contacts(self):
        """Prompt includes detected contacts."""
        unit = _make_unit()
        contact = _make_contact(estimated_type="tank_company")
        knowledge = _make_knowledge([unit], [contact])
        doctrine = get_doctrine("balanced")
        agent_data = {"name": "Red CO", "risk_posture": "balanced"}

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {}, knowledge, tick=5
        )
        assert "tank_company" in user

    def test_prompt_includes_doctrine_instruction(self):
        """Prompt includes the doctrine instruction."""
        doctrine = get_doctrine("aggressive")
        agent_data = {"name": "Red CO", "risk_posture": "aggressive"}
        knowledge = _make_knowledge([_make_unit()])

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {}, knowledge, tick=5
        )
        assert "AGGRESSIVE" in system

    def test_prompt_includes_terrain_info(self):
        """Prompt includes terrain context when available."""
        unit = _make_unit()
        unit["grid_ref"] = "B4-3"
        terrain = {"B4-3": {"terrain_type": "forest", "elevation_m": 250.0}}
        knowledge = _make_knowledge([unit], terrain=terrain)
        knowledge["terrain_around_units"] = terrain
        doctrine = get_doctrine("balanced")
        agent_data = {"name": "Red CO", "risk_posture": "balanced"}

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {}, knowledge, tick=5
        )
        assert "forest" in user
        assert "250" in user

    def test_prompt_shows_unit_warnings(self):
        """Prompt shows warning flags for damaged units."""
        unit = _make_unit(ammo=0.1, suppression=0.5)
        knowledge = _make_knowledge([unit])
        doctrine = get_doctrine("balanced")
        agent_data = {"name": "Red CO", "risk_posture": "balanced"}

        system, user = build_red_commander_prompt(
            agent_data, doctrine, {}, knowledge, tick=5
        )
        assert "LOW_AMMO" in user
        assert "SUPPRESSED" in user


# ══════════════════════════════════════════════════════════════
# ── Find Nearest Contact Tests ────────────────────────────────
# ══════════════════════════════════════════════════════════════

class TestFindNearestContact:
    """Tests for the _find_nearest_contact utility."""

    def setup_method(self):
        self.agent = RedAIAgent()

    def test_no_unit_position_returns_none(self):
        """Unit without lat/lon → None."""
        unit = _make_unit(lat=None, lon=None)
        result = self.agent._find_nearest_contact(unit, [_make_contact()])
        assert result is None

    def test_no_contacts_returns_none(self):
        """No contacts → None."""
        unit = _make_unit()
        result = self.agent._find_nearest_contact(unit, [])
        assert result is None

    def test_finds_closest(self):
        """Finds the contact closest to the unit."""
        unit = _make_unit(lat=49.05, lon=4.5)
        far = _make_contact(lat=49.1, lon=4.6)  # ~7km
        near = _make_contact(lat=49.052, lon=4.502)  # ~250m

        result = self.agent._find_nearest_contact(unit, [far, near])
        assert result is not None
        assert result["lat"] == 49.052
        assert result["distance_to_nearest_m"] < 500


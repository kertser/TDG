"""
IntentInterpreter – deterministic tactical intent extraction.

Fully rule-based: maps parsed order fields to TacticalIntent without LLM.
Uses a comprehensive rules table mapping (order_type, speed, engagement,
purpose keywords, unit_type) → tactical action + implied tasks.

Previously used GPT-4.1-mini but the input is already structured JSON,
making LLM unnecessary. This saves 100% of intent-stage API costs.
"""

from __future__ import annotations

import logging

from backend.schemas.order import TacticalIntent, ParsedOrderData

logger = logging.getLogger(__name__)


# ── Implied task rules (order_type → common implied tasks) ──

_IMPLIED_TASKS = {
    "move": ["maintain communication", "report arrival"],
    "attack": ["suppress enemy fire", "establish fire superiority", "consolidate after assault"],
    "defend": ["improve positions", "establish observation posts", "prepare fire plan"],
    "observe": ["maintain concealment", "report all contacts", "avoid engagement"],
    "support": ["coordinate fires with supported element", "maintain ammunition supply"],
    "fire": ["compute fire solution", "observe effects on target", "adjust fire as needed"],
    "withdraw": ["maintain contact until disengaged", "establish rally point", "report clear"],
    "disengage": ["break contact immediately", "seek nearest covered position", "suppress enemy during withdrawal", "report clear"],
    "halt": ["establish local security", "report status"],
    "regroup": ["consolidate personnel", "redistribute ammunition", "report readiness"],
    "report_status": ["assess unit condition", "count personnel and equipment"],
}

# ── Constraint extraction patterns ──

_CONSTRAINT_PATTERNS = {
    "hold_fire": "do not engage unless engaged",
    "return_fire_only": "return fire only when fired upon",
    "fire_at_will": "engage targets of opportunity",
}


class IntentInterpreter:
    """
    Deterministic tactical intent interpreter.

    Rules-based: examines order_type, speed, engagement rules, purpose text,
    and unit type to determine the best-fit tactical action.
    """

    async def interpret(
        self,
        parsed: ParsedOrderData,
        target_units: list[dict],
    ) -> TacticalIntent | None:
        """
        Interpret tactical intent from a parsed order.

        Args:
            parsed: The structured parsed order.
            target_units: Details of the target units.

        Returns:
            TacticalIntent — always returns a result (deterministic, no failures).
        """
        return self._rule_based_intent(parsed, target_units)

    def _rule_based_intent(
        self,
        parsed: ParsedOrderData,
        target_units: list[dict],
    ) -> TacticalIntent:
        """Comprehensive rule-based tactical intent determination."""
        order_type = parsed.order_type.value if parsed.order_type else "move"
        speed = parsed.speed.value if parsed.speed else None
        engagement = parsed.engagement_rules
        purpose_text = (parsed.purpose or "").lower()
        urgency = parsed.urgency

        # Determine primary unit type for context-sensitive rules
        unit_type = ""
        if target_units:
            unit_type = target_units[0].get("unit_type", "")

        is_recon = "recon" in unit_type or "observation" in unit_type or "sniper" in unit_type
        is_armor = "tank" in unit_type or "mech" in unit_type
        is_artillery = "artillery" in unit_type or "mortar" in unit_type

        # ── Determine tactical action ──
        action = self._determine_action(
            order_type, speed, engagement, purpose_text,
            urgency, is_recon, is_armor, is_artillery,
        )

        # ── Determine purpose ──
        purpose = self._determine_purpose(order_type, purpose_text, action)

        # ── Determine priority ──
        priority = self._determine_priority(urgency, order_type, engagement)

        # ── Determine main effort ──
        main_effort = self._is_main_effort(urgency, purpose_text)

        # ── Build implied tasks ──
        implied = self._build_implied_tasks(action, order_type, engagement, is_recon)

        # ── Build constraints ──
        constraints = self._build_constraints(engagement, speed, purpose_text)

        # ── Suggest formation if none explicitly specified ──
        suggested_formation = None
        if not parsed.formation:
            suggested_formation = self._suggest_formation(
                action, order_type, speed, is_recon, is_armor, target_units,
            )

        intent = TacticalIntent(
            action=action,
            purpose=purpose,
            main_effort=main_effort,
            implied_tasks=implied,
            constraints=constraints,
            priority=priority,
            suggested_formation=suggested_formation,
        )

        logger.info(
            "IntentInterpreter (rules): action=%s priority=%s order=%s speed=%s formation=%s",
            intent.action, intent.priority, order_type, speed,
            parsed.formation or suggested_formation or "none",
        )
        return intent

    def _suggest_formation(
        self,
        action: str,
        order_type: str,
        speed: str | None,
        is_recon: bool,
        is_armor: bool,
        target_units: list[dict],
    ) -> str | None:
        """
        Suggest a tactically appropriate formation when none is explicitly given.

        Rules based on military doctrine:
        - Column: fast movement, road march, low threat
        - Wedge: balanced movement with contact expected (default for advance)
        - Line: assault, maximum firepower forward, close contact
        - Staggered column: recon, cautious movement
        - Vee: armor advance, contact expected from front
        """
        # Artillery / fire support units don't use formations
        unit_type = ""
        if target_units:
            unit_type = target_units[0].get("unit_type", "")
        if "artillery" in unit_type or "mortar" in unit_type:
            return None

        # Defensive postures don't suggest movement formations
        if order_type in ("defend", "observe", "halt", "regroup", "report_status"):
            return None

        # Recon units → staggered column (cautious) or wedge
        if is_recon:
            if speed == "fast":
                return "column"
            return "staggered"

        # Attack / assault → line for maximum firepower
        if action in ("deliberate_attack", "hasty_attack", "fix"):
            if is_armor:
                return "line"  # armor assaults in line
            return "line"

        # Advance to contact → wedge (balanced security)
        if action in ("advance_to_contact", "movement_to_contact"):
            if speed == "fast":
                if is_armor:
                    return "vee"  # armor fast advance
                return "wedge"
            if speed == "slow":
                return "wedge"  # cautious but ready for contact
            return "wedge"  # default for movement to contact

        # Flank maneuver → column (fast, then deploy)
        if action == "flank":
            return "column"

        # Withdraw / disengage → column for speed
        if action in ("withdraw", "disengage"):
            return "column"

        # Patrol → staggered column
        if action == "patrol":
            return "staggered"

        # Road march (fast move, no contact expected) → column
        if order_type == "move" and speed == "fast":
            return "column"

        # Default: wedge (balanced)
        if order_type == "move":
            return "wedge"

        return None

    def _determine_action(
        self,
        order_type: str,
        speed: str | None,
        engagement: str | None,
        purpose_text: str,
        urgency: str | None,
        is_recon: bool,
        is_armor: bool,
        is_artillery: bool,
    ) -> str:
        """Map order fields to a specific tactical action."""

        # ── MOVE orders — most nuanced ──
        if order_type == "move":
            # Recon units → screen/recon
            if is_recon:
                if engagement == "hold_fire":
                    return "screen"
                return "recon"

            # Flanking keywords in purpose
            if any(kw in purpose_text for kw in ["flank", "обход", "фланг", "envelop"]):
                return "flank"

            # Engagement rules suggest expecting contact
            if engagement == "fire_at_will":
                return "advance_to_contact"

            # Speed signals
            if speed == "fast" or urgency in ("immediate", "flash"):
                return "advance_to_contact"
            if speed == "slow":
                return "movement_to_contact"

            # Purpose-based
            if any(kw in purpose_text for kw in ["occupy", "занять", "seize", "захват"]):
                return "occupy"
            if any(kw in purpose_text for kw in ["patrol", "патрул"]):
                return "patrol"
            if any(kw in purpose_text for kw in ["reconn", "развед", "scout", "обнаруж"]):
                return "recon"

            # Default move
            return "movement_to_contact"

        # ── FIRE orders (indirect fire at location) ──
        if order_type == "fire":
            return "support_by_fire"

        # ── ATTACK orders ──
        if order_type == "attack":
            if urgency in ("immediate", "flash") or speed == "fast":
                return "hasty_attack"
            if any(kw in purpose_text for kw in ["destroy", "уничтож", "annihilate"]):
                return "deliberate_attack"
            if any(kw in purpose_text for kw in ["suppress", "подави", "pin", "fix", "сковать"]):
                return "fix"
            if any(kw in purpose_text for kw in ["support", "поддерж", "cover", "прикры"]):
                return "support_by_fire"
            if is_artillery:
                return "support_by_fire"
            return "deliberate_attack"

        # ── DEFEND orders ──
        if order_type == "defend":
            if speed == "fast" or urgency in ("immediate", "flash"):
                return "hasty_defense"
            if any(kw in purpose_text for kw in ["delay", "задерж", "slow"]):
                return "delay"
            return "hold"

        # ── OBSERVE orders ──
        if order_type == "observe":
            if any(kw in purpose_text for kw in ["patrol", "патрул"]):
                return "patrol"
            return "observe"

        # ── SUPPORT orders ──
        if order_type == "support":
            return "support_by_fire"

        # ── WITHDRAW orders ──
        if order_type == "withdraw":
            if any(kw in purpose_text for kw in ["delay", "задерж"]):
                return "delay"
            return "withdraw"

        # ── DISENGAGE orders ──
        if order_type == "disengage":
            return "disengage"

        # ── Simple mappings ──
        simple = {
            "halt": "halt",
            "regroup": "regroup",
            "report_status": "observe",
        }
        return simple.get(order_type, "movement_to_contact")

    def _determine_purpose(self, order_type: str, purpose_text: str, action: str) -> str:
        """Determine the purpose text."""
        if purpose_text:
            return purpose_text

        # Generate default purpose from action
        defaults = {
            "advance_to_contact": "advance and engage enemy",
            "movement_to_contact": "move to objective, expect contact",
            "deliberate_attack": "assault and seize objective",
            "hasty_attack": "quick assault on opportunity",
            "support_by_fire": "provide covering fire",
            "fix": "pin enemy in place",
            "flank": "maneuver to enemy flank",
            "screen": "observe and report enemy activity",
            "recon": "gather intelligence on enemy and terrain",
            "observe": "maintain surveillance of sector",
            "patrol": "patrol area, detect threats",
            "occupy": "occupy and prepare position",
            "hold": "defend current position",
            "hasty_defense": "establish quick defensive position",
            "deliberate_defense": "prepare deliberate defense",
            "delay": "slow enemy advance while withdrawing",
            "withdraw": "disengage and withdraw to new position",
            "disengage": "break contact and seek covered position",
            "regroup": "consolidate and reorganize",
            "halt": "halt movement, establish security",
        }
        return defaults.get(action, f"execute {order_type}")

    def _determine_priority(self, urgency: str | None, order_type: str, engagement: str | None) -> str:
        """Determine priority level."""
        if urgency in ("immediate", "flash"):
            return "high"
        if urgency == "priority":
            return "high"
        if order_type in ("attack", "withdraw", "disengage"):
            return "high"
        if engagement == "fire_at_will":
            return "high"
        if urgency == "routine":
            return "low"
        return "medium"

    def _is_main_effort(self, urgency: str | None, purpose_text: str) -> bool:
        """Determine if this is the main effort."""
        if urgency in ("immediate", "flash"):
            return True
        if any(kw in purpose_text for kw in ["main", "главн", "primary", "основн", "decisive", "решающ"]):
            return True
        return False

    def _build_implied_tasks(
        self, action: str, order_type: str, engagement: str | None, is_recon: bool,
    ) -> list[str]:
        """Build list of implied tasks based on action and context."""
        tasks = list(_IMPLIED_TASKS.get(order_type, []))

        # Action-specific implied tasks
        if action in ("advance_to_contact", "movement_to_contact"):
            tasks.append("establish advance guard")
            if is_recon:
                tasks.append("avoid decisive engagement")
        if action == "deliberate_attack":
            tasks.append("coordinate supporting fires")
            tasks.append("plan consolidation on objective")
        if action == "flank":
            tasks.append("avoid detection during approach")
            tasks.append("coordinate timing with fixing element")
        if action in ("screen", "recon"):
            tasks.append("maintain concealment")
            tasks.append("establish observation posts")
        if action in ("hold", "hasty_defense"):
            tasks.append("prepare alternate positions")
        if action == "withdraw":
            tasks.append("establish rear guard")
        if engagement == "hold_fire":
            tasks.append("maintain fire discipline")

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for t in tasks:
            if t not in seen:
                seen.add(t)
                unique.append(t)
        return unique[:6]  # cap at 6 implied tasks

    def _build_constraints(
        self, engagement: str | None, speed: str | None, purpose_text: str,
    ) -> list[str]:
        """Build list of constraints."""
        constraints = []

        if engagement and engagement in _CONSTRAINT_PATTERNS:
            constraints.append(_CONSTRAINT_PATTERNS[engagement])

        if speed == "slow":
            constraints.append("maintain tactical movement, prioritize concealment")
        elif speed == "fast":
            constraints.append("prioritize speed over concealment")

        # Extract constraints from purpose text
        constraint_kw = {
            "avoid civilian": "minimize civilian impact",
            "radio silence": "maintain radio silence",
            "no artillery": "no indirect fire support",
            "не стрелять": "hold fire",
            "тишина": "maintain radio silence",
        }
        for kw, constraint in constraint_kw.items():
            if kw in purpose_text:
                constraints.append(constraint)

        return constraints[:4]  # cap at 4 constraints


# Singleton
intent_interpreter = IntentInterpreter()




"""
Pydantic v2 schemas for the LLM order-parsing pipeline.

These models serve as the **validation boundary** between LLM output and
the authoritative backend.  Every field that the LLM produces is typed
and constrained here; anything that doesn't parse is rejected/retried.
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────

class MessageClassification(str, enum.Enum):
    """High-level classification of a radio message."""
    command = "command"                  # actionable order (move, attack, …)
    status_request = "status_request"   # "доложите обстановку", "report status"
    acknowledgment = "acknowledgment"   # "так точно", "roger"
    status_report = "status_report"     # "находимся в …", "enemy spotted …"
    unclear = "unclear"                 # garbled, irrelevant, incomplete


class DetectedLanguage(str, enum.Enum):
    en = "en"
    ru = "ru"


class OrderType(str, enum.Enum):
    """Tactical order types the engine can execute."""
    move = "move"
    attack = "attack"
    fire = "fire"
    defend = "defend"
    observe = "observe"
    support = "support"
    withdraw = "withdraw"
    disengage = "disengage"
    halt = "halt"
    regroup = "regroup"
    resupply = "resupply"
    request_fire = "request_fire"
    report_status = "report_status"


class SpeedMode(str, enum.Enum):
    slow = "slow"
    fast = "fast"


class ResponseType(str, enum.Enum):
    ack = "ack"                       # acknowledgment
    wilco = "wilco"                   # will comply (movement)
    wilco_fire = "wilco_fire"         # will comply — fire mission (artillery/mortar)
    wilco_request_fire = "wilco_request_fire"  # will comply — requesting CoC fire support
    wilco_observe = "wilco_observe"   # will comply — observe/defend/halt (stationary)
    wilco_standby = "wilco_standby"   # will comply — standby to support another unit
    wilco_disengage = "wilco_disengage"  # will comply — disengage/break contact
    wilco_resupply = "wilco_resupply"    # will comply — resupply mission
    unable = "unable"                 # cannot comply
    unable_range = "unable_range"     # cannot comply — target beyond max fire range
    unable_area = "unable_area"       # cannot comply — target outside operations area
    unable_route = "unable_route"     # cannot comply — no passable route to destination
    clarify = "clarify"              # request clarification
    status = "status"                # status report
    no_response = "no_response"      # comms down / destroyed


# ── Location reference (LLM-extracted, pre-resolution) ──────────

class LocationRefRaw(BaseModel):
    """A location reference extracted verbatim from the order text."""
    source_text: str = Field(..., description="The original text fragment, e.g. 'B8 по улитке 2-4'")
    ref_type: str = Field(
        "unknown",
        description="Best guess: 'grid', 'snail', 'coordinate', 'height', 'relative', 'terrain', 'unknown'",
    )
    normalized: str = Field(
        "",
        description="Normalized form if parseable, e.g. 'B8-2-4', '48.85,2.35', 'southeast'",
    )


# ── Parsed order (LLM structured output) ────────────────────────

class ParsedOrderData(BaseModel):
    """
    Structured representation of an actionable command order,
    produced by the OrderParser LLM call.
    """
    classification: MessageClassification
    language: DetectedLanguage = DetectedLanguage.en

    # Target unit identification (as mentioned in the text)
    target_unit_refs: list[str] = Field(
        default_factory=list,
        description="Unit names/callsigns mentioned as targets of the order, "
                    "e.g. ['Первый взвод', '2nd Platoon', 'Группа 2-12']",
    )
    # Who is speaking / issuing (if identifiable from text)
    sender_ref: Optional[str] = Field(
        None,
        description="Callsign/name of the sender if identifiable from text",
    )

    # For command messages
    order_type: Optional[OrderType] = None
    location_refs: list[LocationRefRaw] = Field(
        default_factory=list,
        description="All location references extracted from the text",
    )
    speed: Optional[SpeedMode] = None
    formation: Optional[str] = None
    engagement_rules: Optional[str] = Field(
        None,
        description="Engagement constraints: 'fire_at_will', 'hold_fire', 'return_fire_only', etc.",
    )
    urgency: Optional[str] = Field(
        None,
        description="'routine', 'priority', 'immediate', 'flash'",
    )
    purpose: Optional[str] = Field(
        None,
        description="Stated purpose/objective, e.g. 'обнаружение и уничтожение противника'",
    )
    support_target_ref: Optional[str] = Field(
        None,
        description="Unit name/callsign that this unit should support/relay to, "
                    "e.g. 'C-squad' in 'be ready to support C-squad's targets'",
    )
    status_request_focus: list[str] = Field(
        default_factory=list,
        description="For status_request messages: requested info categories such as "
                    "'full', 'position', 'terrain', 'nearby_friendlies', 'enemy', "
                    "'task', 'condition', 'weather', 'objects'",
    )

    # For acknowledgment / status_report messages
    report_text: Optional[str] = Field(
        None,
        description="Key content of a status report or acknowledgment",
    )

    # Confidence & ambiguity
    confidence: float = Field(
        1.0, ge=0.0, le=1.0,
        description="Parser confidence in the interpretation (0-1)",
    )
    ambiguities: list[str] = Field(
        default_factory=list,
        description="List of unclear/ambiguous elements in the order",
    )


# ── Tactical intent (IntentInterpreter output) ──────────────────

class TacticalIntent(BaseModel):
    """
    Higher-level tactical interpretation of an order.
    """
    action: str = Field(
        ...,
        description="Primary tactical action: 'advance_to_contact', 'deliberate_attack', "
                    "'hasty_defense', 'screen', 'recon_by_force', 'movement_to_contact', "
                    "'support_by_fire', 'fix', 'flank', 'delay', 'withdraw', "
                    "'occupy', 'hold', 'observe', 'patrol'",
    )
    purpose: Optional[str] = Field(None, description="Why: destroy, fix, screen, delay, seize …")
    main_effort: bool = Field(False, description="Is this the main effort?")
    implied_tasks: list[str] = Field(
        default_factory=list,
        description="Tasks implied but not stated, e.g. 'establish observation post', 'report contact'",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Constraints on execution, e.g. 'avoid civilian areas', 'maintain radio silence'",
    )
    priority: Optional[str] = Field(None, description="'high', 'medium', 'low'")
    suggested_formation: Optional[str] = Field(
        None,
        description="Tactically appropriate formation suggested by doctrine rules when "
                    "none was explicitly ordered: column, line, wedge, vee, etc.",
    )


# ── Resolved location (after LocationResolver) ──────────────────

class ResolvedLocation(BaseModel):
    """A location reference after deterministic resolution."""
    source_text: str
    ref_type: str               # 'grid', 'snail', 'coordinate', 'relative'
    normalized_ref: str          # e.g. 'B8-2-4' or '48.85,2.35'
    lat: Optional[float] = None
    lon: Optional[float] = None
    confidence: float = 1.0
    resolution_depth: Optional[int] = None


# ── Unit radio response ──────────────────────────────────────────

class UnitRadioResponse(BaseModel):
    """A radio-style response from a unit."""
    from_unit_name: str
    from_unit_id: Optional[str] = None
    text: str
    language: DetectedLanguage = DetectedLanguage.en
    response_type: ResponseType = ResponseType.ack


# ── Full pipeline result ─────────────────────────────────────────

class OrderParseResult(BaseModel):
    """
    Complete result of the order-processing pipeline.
    Bundles classification, parsed order, resolved locations,
    tactical intent, and unit response(s).
    """
    parsed: ParsedOrderData
    resolved_locations: list[ResolvedLocation] = Field(default_factory=list)
    intent: Optional[TacticalIntent] = None
    responses: list[UnitRadioResponse] = Field(default_factory=list)
    # IDs of matched units (resolved from target_unit_refs)
    matched_unit_ids: list[str] = Field(default_factory=list)
    # Task dict ready for the tick engine (matches _order_to_task format)
    engine_task: Optional[dict] = None


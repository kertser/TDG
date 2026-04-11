"""
Data structures for collecting tick-by-tick simulation data and results.
"""
from __future__ import annotations

import uuid
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class UnitSnapshot:
    """Snapshot of a single unit's state at a point in time."""
    id: str
    name: str
    side: str
    unit_type: str
    lat: float
    lon: float
    strength: float
    ammo: float
    morale: float
    suppression: float
    is_destroyed: bool
    current_task: dict | None
    heading_deg: float
    comms_status: str


@dataclass
class TickSnapshot:
    """Complete snapshot of game state at one tick."""
    tick: int
    game_time: str | None
    units: list[UnitSnapshot] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    contacts: list[dict] = field(default_factory=list)
    radio_messages: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    tick_result: dict = field(default_factory=dict)


@dataclass
class OrderSnapshot:
    """Snapshot of LLM pipeline processing for one order."""
    original_text: str
    target_unit_names: list[str]
    side: str
    inject_tick: int
    # LLM pipeline outputs
    classification: str | None = None        # command, status_request, ack, etc.
    order_type: str | None = None            # move, attack, fire, etc.
    language: str | None = None              # en, ru
    confidence: float = 0.0
    target_unit_refs: list[str] = field(default_factory=list)
    locations_resolved: list[dict] = field(default_factory=list)
    model_tier: str | None = None            # keyword, nano, full
    # Expected values for assertion comparison
    expected_classification: str | None = None
    expected_order_type: str | None = None
    expected_language: str | None = None
    expected_locations: list[str] = field(default_factory=list)
    expected_model_tier: str | None = None
    # Raw pipeline result
    pipeline_result: dict | None = None
    error: str | None = None


@dataclass
class AssertionResult:
    """Result of evaluating one assertion."""
    assertion_type: str
    description: str
    passed: bool
    detail: str = ""


@dataclass
class ScenarioResult:
    """Complete result of running one test scenario."""
    scenario_name: str
    scenario_description: str
    ticks_run: int
    snapshots: list[TickSnapshot] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    passed: bool = False
    # LLM pipeline results
    order_snapshots: list[OrderSnapshot] = field(default_factory=list)
    category: str = "engine"
    run_index: int = 0  # for statistical multi-run tracking

    @property
    def assertions_passed(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def assertions_total(self) -> int:
        return len(self.assertions)

    def all_events(self) -> list[dict]:
        """Flatten all events across all ticks."""
        result = []
        for snap in self.snapshots:
            result.extend(snap.events)
        return result

    def final_units(self) -> list[UnitSnapshot]:
        """Get the final tick's unit states."""
        if self.snapshots:
            return self.snapshots[-1].units
        return []

    def find_unit(self, name: str, tick: int = -1) -> UnitSnapshot | None:
        """Find a unit by name at a specific tick (-1 = last)."""
        if not self.snapshots:
            return None
        snap = self.snapshots[tick]
        for u in snap.units:
            if u.name == name:
                return u
        return None

    def unit_history(self, name: str) -> list[tuple[int, UnitSnapshot]]:
        """Get (tick, snapshot) pairs for a named unit across all ticks."""
        result = []
        for snap in self.snapshots:
            for u in snap.units:
                if u.name == name:
                    result.append((snap.tick, u))
                    break
        return result


@dataclass
class StatisticalResult:
    """Aggregated result from multiple runs of the same scenario."""
    scenario_name: str
    scenario_description: str
    runs: list[ScenarioResult] = field(default_factory=list)
    assertions: list[AssertionResult] = field(default_factory=list)
    passed: bool = False
    total_duration: float = 0.0

    @property
    def num_runs(self) -> int:
        return len(self.runs)

    @property
    def assertions_passed(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def assertions_total(self) -> int:
        return len(self.assertions)

    def detection_ticks(self, observer_side: str = "blue") -> list[int]:
        """First tick when detection occurred in each run."""
        results = []
        for run in self.runs:
            first_tick = None
            for snap in run.snapshots:
                for c in snap.contacts:
                    if c.get("observing_side") == observer_side:
                        first_tick = snap.tick
                        break
                if first_tick is not None:
                    break
            results.append(first_tick if first_tick is not None else -1)
        return results

    def detection_rate(self, observer_side: str = "blue") -> float:
        """Fraction of runs where detection occurred."""
        ticks = self.detection_ticks(observer_side)
        detected = sum(1 for t in ticks if t >= 0)
        return detected / max(1, len(ticks))

    def final_strengths(self, unit_name: str) -> list[float]:
        """Final strength of a unit across all runs."""
        results = []
        for run in self.runs:
            u = run.find_unit(unit_name)
            results.append(u.strength if u and not u.is_destroyed else 0.0)
        return results

    def mean_strength(self, unit_name: str) -> float:
        vals = self.final_strengths(unit_name)
        return sum(vals) / max(1, len(vals))

    def stddev_strength(self, unit_name: str) -> float:
        vals = self.final_strengths(unit_name)
        if len(vals) < 2:
            return 0.0
        mean = sum(vals) / len(vals)
        return math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))

    def combat_event_counts(self) -> list[int]:
        """Number of combat events per run."""
        results = []
        for run in self.runs:
            count = sum(1 for e in run.all_events() if e.get("event_type") == "combat")
            results.append(count)
        return results

    def destroyed_counts(self, side: str | None = None) -> list[int]:
        """Number of destroyed units per run."""
        results = []
        for run in self.runs:
            count = 0
            if run.snapshots:
                final = run.snapshots[-1]
                for u in final.units:
                    if u.is_destroyed:
                        if side is None or u.side == side:
                            count += 1
            results.append(count)
        return results


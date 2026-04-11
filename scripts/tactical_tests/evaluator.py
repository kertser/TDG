"""
Evaluator — checks scenario results against expected assertions.

Assertion types:
  Engine assertions:
  - unit_survives: unit is not destroyed at end
  - unit_destroyed: unit IS destroyed at end
  - unit_strength_above: final strength > threshold
  - unit_strength_below: final strength < threshold
  - unit_reached_area: unit was within radius of target at some tick
  - detection_occurs: at least N contacts created
  - event_exists: at least one event of given type
  - event_count_min: at least N events of given type
  - no_event: assert event type does NOT occur
  - order_completed: order_completed event for a unit
  - contact_count_min: contacts for a side >= N
  - unit_moved: unit position changed from initial
  - unit_task_is: unit has a specific task type at end
  - unit_ammo_above: ammo above threshold at end
  - unit_morale_above: morale above threshold at end
  - custom: arbitrary callable

  LLM assertions:
  - llm_classification_is: verify parsed classification
  - llm_order_type_is: verify extracted order type
  - llm_language_detected: verify EN/RU detection
  - llm_location_resolved: verify location was resolved
  - llm_confidence_above: verify parsing confidence
  - llm_not_nonsense: verify classification is NOT unclear for good orders

  Statistical assertions:
  - stat_detection_rate: detection probability within range
  - stat_mean_strength: mean unit strength above/below threshold
  - stat_combat_occurs_always: combat events in every run
  - stat_outcome_varies: different outcomes across runs (non-zero variance)
"""
from __future__ import annotations

import math
from scripts.tactical_tests.collector import ScenarioResult, StatisticalResult, AssertionResult, UnitSnapshot

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON = 74_000.0


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON
    return math.sqrt(dlat * dlat + dlon * dlon)


class Evaluator:
    """Evaluate scenario results against assertions."""

    def evaluate(self, result: ScenarioResult, assertions: list[dict]) -> list[AssertionResult]:
        """Run all assertions and return results."""
        results = []
        for assertion in assertions:
            a_type = assertion["type"]
            params = assertion.get("params", {})
            desc = assertion.get("description", f"{a_type}")

            handler = getattr(self, f"_check_{a_type}", None)
            if handler:
                try:
                    passed, detail = handler(result, params)
                except Exception as e:
                    passed, detail = False, f"Assertion error: {e}"
            else:
                passed, detail = False, f"Unknown assertion type: {a_type}"

            results.append(AssertionResult(
                assertion_type=a_type,
                description=desc,
                passed=passed,
                detail=detail,
            ))

        return results

    # ── Existing engine assertions ──

    def _check_unit_survives(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.is_destroyed:
            return False, f"Unit '{name}' was destroyed (strength={unit.strength:.3f})"
        return True, f"Unit '{name}' survived (strength={unit.strength:.3f}, morale={unit.morale:.3f})"

    def _check_unit_destroyed(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if not unit.is_destroyed:
            return False, f"Unit '{name}' survived with strength={unit.strength:.3f}"
        return True, f"Unit '{name}' was destroyed"

    def _check_unit_strength_above(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        threshold = params["threshold"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.strength < threshold:
            return False, f"Unit '{name}' strength {unit.strength:.3f} < {threshold}"
        return True, f"Unit '{name}' strength {unit.strength:.3f} >= {threshold}"

    def _check_unit_strength_below(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        threshold = params["threshold"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.strength > threshold:
            return False, f"Unit '{name}' strength {unit.strength:.3f} > {threshold}"
        return True, f"Unit '{name}' strength {unit.strength:.3f} <= {threshold}"

    def _check_unit_reached_area(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        target_lat = params["lat"]
        target_lon = params["lon"]
        radius_m = params.get("radius_m", 100)

        for snap in result.snapshots:
            for u in snap.units:
                if u.name == name and not u.is_destroyed:
                    dist = _distance_m(u.lat, u.lon, target_lat, target_lon)
                    if dist <= radius_m:
                        return True, f"Unit '{name}' reached target at tick {snap.tick} (dist={dist:.0f}m)"

        return False, f"Unit '{name}' never reached within {radius_m}m of ({target_lat:.4f}, {target_lon:.4f})"

    def _check_detection_occurs(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        side = params.get("observer_side")
        min_count = params.get("min_count", 1)

        total_contacts = 0
        for snap in result.snapshots:
            for c in snap.contacts:
                if side is None or c.get("observing_side") == side:
                    total_contacts += 1

        # Deduplicate by looking at the max contacts at any tick
        max_contacts = 0
        for snap in result.snapshots:
            tick_contacts = sum(1 for c in snap.contacts if side is None or c.get("observing_side") == side)
            max_contacts = max(max_contacts, tick_contacts)

        if max_contacts >= min_count:
            return True, f"Detection occurred: {max_contacts} contacts for {side or 'any'} side (need {min_count})"
        return False, f"Only {max_contacts} contacts for {side or 'any'} side (need {min_count})"

    def _check_event_exists(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        event_type = params["event_type"]
        all_events = result.all_events()
        matches = [e for e in all_events if e.get("event_type") == event_type]
        if matches:
            first = matches[0]
            return True, f"Found {len(matches)} '{event_type}' events. First: {first.get('text_summary', '')[:80]}"
        return False, f"No '{event_type}' events found"

    def _check_event_count_min(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        event_type = params["event_type"]
        min_count = params["count"]
        all_events = result.all_events()
        matches = [e for e in all_events if e.get("event_type") == event_type]
        if len(matches) >= min_count:
            return True, f"Found {len(matches)} '{event_type}' events (need {min_count})"
        return False, f"Only {len(matches)} '{event_type}' events (need {min_count})"

    def _check_no_event(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        event_type = params["event_type"]
        all_events = result.all_events()
        matches = [e for e in all_events if e.get("event_type") == event_type]
        if not matches:
            return True, f"No '{event_type}' events — good"
        first = matches[0]
        return False, f"Found {len(matches)} '{event_type}' events! First: {first.get('text_summary', '')[:80]}"

    def _check_order_completed(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params.get("unit_name")
        unit_id = None
        for snap in result.snapshots:
            for u in snap.units:
                if u.name == name:
                    unit_id = u.id
                    break
            if unit_id:
                break

        for snap in result.snapshots:
            for e in snap.events:
                if e.get("event_type") == "order_completed":
                    if unit_id and str(e.get("actor_unit_id")) == unit_id:
                        return True, f"Order completed for '{name}'"

        return False, f"No order_completed event for '{name}'"

    def _check_contact_count_min(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        side = params["side"]
        min_count = params["count"]

        max_contacts = 0
        for snap in result.snapshots:
            tick_contacts = sum(1 for c in snap.contacts if c.get("observing_side") == side)
            max_contacts = max(max_contacts, tick_contacts)

        if max_contacts >= min_count:
            return True, f"Side '{side}' had {max_contacts} contacts (need {min_count})"
        return False, f"Side '{side}' only had {max_contacts} contacts (need {min_count})"

    def _check_unit_moved(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        min_distance_m = params.get("min_distance_m", 50)

        history = result.unit_history(name)
        if len(history) < 2:
            return False, f"Unit '{name}' has insufficient history"

        first_tick, first_state = history[0]
        last_tick, last_state = history[-1]

        if first_state.is_destroyed or last_state.is_destroyed:
            return False, f"Unit '{name}' was destroyed"

        dist = _distance_m(first_state.lat, first_state.lon, last_state.lat, last_state.lon)
        if dist >= min_distance_m:
            return True, f"Unit '{name}' moved {dist:.0f}m from start to end"
        return False, f"Unit '{name}' only moved {dist:.0f}m (need {min_distance_m}m)"

    def _check_custom(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        fn = params.get("fn")
        if fn and callable(fn):
            return fn(result)
        return False, "No custom function provided"

    # ── New engine assertions ──

    def _check_unit_task_is(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        expected_type = params["task_type"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.is_destroyed:
            return False, f"Unit '{name}' is destroyed"
        actual = unit.current_task.get("type", "idle") if unit.current_task else "idle"
        if actual == expected_type:
            return True, f"Unit '{name}' task is '{actual}'"
        return False, f"Unit '{name}' task is '{actual}', expected '{expected_type}'"

    def _check_unit_ammo_above(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        threshold = params["threshold"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.ammo >= threshold:
            return True, f"Unit '{name}' ammo {unit.ammo:.3f} >= {threshold}"
        return False, f"Unit '{name}' ammo {unit.ammo:.3f} < {threshold}"

    def _check_unit_morale_above(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        threshold = params["threshold"]
        unit = result.find_unit(name)
        if unit is None:
            return False, f"Unit '{name}' not found"
        if unit.morale >= threshold:
            return True, f"Unit '{name}' morale {unit.morale:.3f} >= {threshold}"
        return False, f"Unit '{name}' morale {unit.morale:.3f} < {threshold}"

    # ── LLM pipeline assertions ──

    def _check_llm_classification_is(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that a specific order was classified correctly."""
        order_idx = params.get("order_index", 0)
        expected = params["expected"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found (only {len(result.order_snapshots)} orders)"
        snap = result.order_snapshots[order_idx]
        if snap.error:
            return False, f"Order failed: {snap.error}"
        actual = snap.classification
        if actual == expected:
            return True, f"Classification '{actual}' matches expected '{expected}' (conf={snap.confidence:.2f})"
        return False, f"Classification '{actual}' != expected '{expected}' (text: '{snap.original_text[:60]}')"

    def _check_llm_classification_in(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that classification is one of multiple valid values."""
        order_idx = params.get("order_index", 0)
        expected_list = params["expected"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.error:
            return False, f"Order failed: {snap.error}"
        actual = snap.classification
        if actual in expected_list:
            return True, f"Classification '{actual}' is in {expected_list} (conf={snap.confidence:.2f})"
        return False, f"Classification '{actual}' not in {expected_list} (text: '{snap.original_text[:60]}')"

    def _check_llm_order_type_is(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that extracted order type matches."""
        order_idx = params.get("order_index", 0)
        expected = params["expected"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.error:
            return False, f"Order failed: {snap.error}"
        actual = snap.order_type
        if actual == expected:
            return True, f"OrderType '{actual}' matches (conf={snap.confidence:.2f})"
        return False, f"OrderType '{actual}' != expected '{expected}'"

    def _check_llm_order_type_in(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that order type is one of multiple valid values."""
        order_idx = params.get("order_index", 0)
        expected_list = params["expected"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.error:
            return False, f"Order failed: {snap.error}"
        actual = snap.order_type
        if actual in expected_list:
            return True, f"OrderType '{actual}' is in {expected_list} (conf={snap.confidence:.2f})"
        return False, f"OrderType '{actual}' not in {expected_list}"

    def _check_llm_language_detected(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        order_idx = params.get("order_index", 0)
        expected = params["expected"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        actual = snap.language
        if actual == expected:
            return True, f"Language '{actual}' detected correctly"
        return False, f"Language '{actual}' != expected '{expected}'"

    def _check_llm_location_resolved(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that at least one location was resolved from the order."""
        order_idx = params.get("order_index", 0)
        min_count = params.get("min_count", 1)
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        count = len(snap.locations_resolved)
        if count >= min_count:
            locs = ", ".join(str(l) for l in snap.locations_resolved[:3])
            return True, f"Resolved {count} location(s): {locs}"
        return False, f"Only {count} location(s) resolved, need {min_count}"

    def _check_llm_confidence_above(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        order_idx = params.get("order_index", 0)
        threshold = params["threshold"]
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.confidence >= threshold:
            return True, f"Confidence {snap.confidence:.2f} >= {threshold}"
        return False, f"Confidence {snap.confidence:.2f} < {threshold}"

    def _check_llm_not_nonsense(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that order was NOT classified as 'unclear'."""
        order_idx = params.get("order_index", 0)
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.classification != "unclear":
            return True, f"Classified as '{snap.classification}' (not unclear)"
        return False, f"Classified as 'unclear' — expected a valid classification"

    def _check_llm_is_nonsense(self, result: ScenarioResult, params: dict) -> tuple[bool, str]:
        """Check that a nonsense order IS classified as 'unclear'."""
        order_idx = params.get("order_index", 0)
        if order_idx >= len(result.order_snapshots):
            return False, f"Order index {order_idx} not found"
        snap = result.order_snapshots[order_idx]
        if snap.classification == "unclear":
            return True, f"Correctly classified as 'unclear'"
        return False, f"Expected 'unclear', got '{snap.classification}'"

    # ── Statistical assertions ──

    def evaluate_statistical(self, stat_result: StatisticalResult, assertions: list[dict]) -> list[AssertionResult]:
        """Run statistical assertions against aggregated multi-run results."""
        results = []
        for assertion in assertions:
            a_type = assertion["type"]
            params = assertion.get("params", {})
            desc = assertion.get("description", f"{a_type}")

            handler = getattr(self, f"_check_{a_type}", None)
            if handler:
                try:
                    passed, detail = handler(stat_result, params)
                except Exception as e:
                    passed, detail = False, f"Assertion error: {e}"
            else:
                passed, detail = False, f"Unknown statistical assertion type: {a_type}"

            results.append(AssertionResult(
                assertion_type=a_type,
                description=desc,
                passed=passed,
                detail=detail,
            ))
        return results

    def _check_stat_detection_rate(self, stat_result: StatisticalResult, params: dict) -> tuple[bool, str]:
        side = params.get("observer_side", "blue")
        min_rate = params.get("min_rate", 0.3)
        max_rate = params.get("max_rate", 1.0)
        rate = stat_result.detection_rate(side)
        if min_rate <= rate <= max_rate:
            return True, f"Detection rate {rate:.1%} in range [{min_rate:.0%}, {max_rate:.0%}] ({stat_result.num_runs} runs)"
        return False, f"Detection rate {rate:.1%} outside [{min_rate:.0%}, {max_rate:.0%}] ({stat_result.num_runs} runs)"

    def _check_stat_mean_strength(self, stat_result: StatisticalResult, params: dict) -> tuple[bool, str]:
        name = params["unit_name"]
        min_val = params.get("min", 0.0)
        max_val = params.get("max", 1.0)
        mean = stat_result.mean_strength(name)
        std = stat_result.stddev_strength(name)
        if min_val <= mean <= max_val:
            return True, f"Mean strength {mean:.3f} ± {std:.3f} in [{min_val}, {max_val}]"
        return False, f"Mean strength {mean:.3f} ± {std:.3f} outside [{min_val}, {max_val}]"

    def _check_stat_combat_occurs_always(self, stat_result: StatisticalResult, params: dict) -> tuple[bool, str]:
        counts = stat_result.combat_event_counts()
        zeros = sum(1 for c in counts if c == 0)
        if zeros == 0:
            return True, f"Combat occurred in all {len(counts)} runs (avg {sum(counts)/len(counts):.1f} events)"
        return False, f"No combat in {zeros}/{len(counts)} runs"

    def _check_stat_outcome_varies(self, stat_result: StatisticalResult, params: dict) -> tuple[bool, str]:
        """Check that outcomes actually vary across runs (non-deterministic detection)."""
        unit_name = params.get("unit_name")
        if unit_name:
            vals = stat_result.final_strengths(unit_name)
        else:
            vals = [float(c) for c in stat_result.combat_event_counts()]
        
        if len(vals) < 2:
            return False, "Need at least 2 runs"
        unique = len(set(f"{v:.3f}" for v in vals))
        if unique > 1:
            mean = sum(vals) / len(vals)
            std = math.sqrt(sum((v - mean) ** 2 for v in vals) / (len(vals) - 1))
            return True, f"Outcome varies: {unique} unique values, mean={mean:.3f} σ={std:.3f}"
        return False, f"All {len(vals)} runs had identical outcome ({vals[0]:.3f})"

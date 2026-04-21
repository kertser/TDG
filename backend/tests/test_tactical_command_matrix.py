from __future__ import annotations

import pytest

from backend.schemas.order import MessageClassification, OrderType
from backend.services.order_parser import order_parser
from backend.services.order_phrasebook import get_order_phrasebook_cases


_PHRASEBOOK_CASES = get_order_phrasebook_cases()


def test_phrasebook_case_ids_are_unique():
    ids = [case["id"] for case in _PHRASEBOOK_CASES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("case", _PHRASEBOOK_CASES, ids=[case["id"] for case in _PHRASEBOOK_CASES])
def test_tactical_command_phrasebook(case: dict):
    parsed = order_parser._fallback_parse(case["text"])

    assert parsed.classification == MessageClassification(case["classification"]), case["text"]

    expected_order_type = case.get("order_type")
    actual_order_type = parsed.order_type.value if parsed.order_type else None
    assert actual_order_type == expected_order_type, case["text"]

    if "speed" in case:
        actual_speed = parsed.speed.value if parsed.speed else None
        assert actual_speed == case["speed"], case["text"]

    if "formation" in case:
        assert parsed.formation == case["formation"], case["text"]

    if "map_object_type" in case:
        assert parsed.map_object_type == case["map_object_type"], case["text"]

    if "coordination_kind" in case:
        assert parsed.coordination_kind == case["coordination_kind"], case["text"]

    if "maneuver_kind" in case:
        assert parsed.maneuver_kind == case["maneuver_kind"], case["text"]

    if "maneuver_side" in case:
        assert parsed.maneuver_side == case["maneuver_side"], case["text"]

    if "merge_target_ref" in case:
        assert parsed.merge_target_ref == case["merge_target_ref"], case["text"]

    if "support_target_ref" in case:
        assert parsed.support_target_ref == case["support_target_ref"], case["text"]

    if "split_ratio" in case:
        assert parsed.split_ratio == pytest.approx(case["split_ratio"]), case["text"]

    if "location_ref_types_contains" in case:
        actual_ref_types = [loc.ref_type for loc in parsed.location_refs]
        for ref_type in case["location_ref_types_contains"]:
            assert ref_type in actual_ref_types, case["text"]

    if "location_normalized_contains" in case:
        actual_normalized = [loc.normalized for loc in parsed.location_refs]
        for normalized in case["location_normalized_contains"]:
            assert normalized in actual_normalized, case["text"]

    if "coordination_unit_refs_contains" in case:
        for ref in case["coordination_unit_refs_contains"]:
            assert ref in parsed.coordination_unit_refs, case["text"]

    if "status_request_focus_contains" in case:
        for focus in case["status_request_focus_contains"]:
            assert focus in parsed.status_request_focus, case["text"]

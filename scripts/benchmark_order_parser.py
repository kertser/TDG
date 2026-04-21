r"""Benchmark the real TDG order parser against the local OpenAI-compatible LLM.

This script exercises the current retrieval + prompt-packing path rather than a toy prompt.

Usage:
    .\venv\Scripts\python.exe scripts\benchmark_order_parser.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
import sys

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.services.order_parser import order_parser


@dataclass(frozen=True)
class BenchCase:
    label: str
    text: str
    expected_classification: str
    expected_order_type: str | None


def sample_units() -> list[dict]:
    return [
        {
            "id": "u-eng",
            "name": "Combat engineers",
            "unit_type": "combat_engineer_section",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.95,
            "ammo": 0.9,
            "morale": 0.92,
            "current_task": {"type": "idle"},
        },
        {
            "id": "u-mortar",
            "name": "Mortar Section",
            "unit_type": "mortar_section",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.9,
            "ammo": 0.95,
            "morale": 0.92,
            "current_task": {"type": "fire"},
        },
        {
            "id": "u-rifle",
            "name": "B-squad",
            "unit_type": "infantry_squad",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.82,
            "ammo": 0.7,
            "morale": 0.88,
            "current_task": {"type": "move"},
        },
        {
            "id": "u-recon",
            "name": "Recon Team",
            "unit_type": "recon_team",
            "side": "blue",
            "is_destroyed": False,
            "comms_status": "operational",
            "strength": 0.96,
            "ammo": 0.8,
            "morale": 0.93,
            "current_task": {"type": "observe"},
        },
    ]


def sample_context() -> dict[str, str | dict]:
    return {
        "grid_info": {
            "columns": 10,
            "rows": 10,
            "labeling_scheme": "alphanumeric",
            "height_tops": [
                {"label": "Hill 170", "label_ru": "Высота 170", "snail_path": "E6-3", "elevation_m": 170.0},
                {"label": "Hill 201", "label_ru": "Высота 201", "snail_path": "F7-4", "elevation_m": 201.0},
            ],
        },
        "game_time": "2026-04-17T12:00:00Z",
        "terrain_context": (
            "Terrain near friendly units:\n"
            "  - Combat engineers: road (E6-3, elev 171m, slope 4.0°)\n"
            "  - B-squad: forest edge (E5-9, elev 165m, slope 7.0°)\n"
            "  - Recon Team: hilltop (F7-4, elev 201m, slope 3.0°)"
        ),
        "contacts_context": (
            "Known enemy contacts (3 active):\n"
            "  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]\n"
            "  - infantry platoon at (48.1500, 24.7100), grid E6-2 [conf=80%, src=visual]\n"
            "  - truck column at (48.1800, 24.7600), grid F7-1 [conf=40%, src=report]"
        ),
        "objectives_context": "Mission: Seize the eastern bridge crossing and keep the lane open.",
        "friendly_status_context": (
            "Friendly forces (4 units): avg strength=91%, ammo=84%, morale=91%\n"
            "  - Combat engineers: strength=95%, ammo=90%, morale=92%, task=idle\n"
            "  - Mortar Section: strength=90%, ammo=95%, morale=92%, task=fire\n"
            "  - B-squad: strength=82%, ammo=70%, morale=88%, task=move"
        ),
        "environment_context": "Environment: weather=clear, visibility=good, wind=light",
        "orders_context": (
            "Recent own-side orders (3):\n"
            "  - 2026-04-17T11:55:00Z: move -> B-squad [executing] | \"Move to the eastern bridge\"\n"
            "  - 2026-04-17T11:57:00Z: request_fire -> Mortar Section [validated] | \"Smoke the crossing\"\n"
            "  - 2026-04-17T11:58:00Z: breach -> Combat engineers [validated] | \"Open a lane at E6-3\""
        ),
        "radio_context": (
            "Recent radio/chat traffic (3 msgs):\n"
            "  - 2026-04-17T11:58:00Z [UNIT_RADIO] B-squad -> HQ: Approaching the bridge crossing.\n"
            "  - 2026-04-17T11:59:00Z [UNIT_RADIO] Mortar Section -> HQ: Same target can be re-engaged with smoke.\n"
            "  - 2026-04-17T11:59:30Z [UNIT_RADIO] Combat engineers -> HQ: Roadblock in sight at E6-3."
        ),
        "reports_context": (
            "Recent operational reports (2):\n"
            "  - 2026-04-17T11:59:30Z [spotrep] from HQ: Enemy machine-gun still covers the crossing.\n"
            "  - 2026-04-17T11:59:45Z [sitrep] from HQ: Crossing must be opened for follow-on forces."
        ),
        "map_objects_context": (
            "Known map objects / points of interest (3):\n"
            "  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)\n"
            "  - Roadblock Alpha (roadblock, category=obstacle, side=red) at (48.1502, 24.7101)\n"
            "  - Smoke screen Bravo (smoke, category=effect, side=blue) at (48.1499, 24.7099)"
        ),
    }


async def run_case(case: BenchCase, *, repeat_label: str = "") -> dict:
    units = sample_units()
    ctx = sample_context()
    keyword_hint = order_parser._fallback_parse(case.text)
    bundle = order_parser._build_prompt_bundle(
        original_text=case.text,
        units=units,
        grid_info=ctx["grid_info"],
        game_time=ctx["game_time"],
        model=settings.LOCAL_MODEL_NAME,
        issuer_side="blue",
        terrain_context=ctx["terrain_context"],
        contacts_context=ctx["contacts_context"],
        objectives_context=ctx["objectives_context"],
        friendly_status_context=ctx["friendly_status_context"],
        environment_context=ctx["environment_context"],
        orders_context=ctx["orders_context"],
        radio_context=ctx["radio_context"],
        reports_context=ctx["reports_context"],
        map_objects_context=ctx["map_objects_context"],
        keyword_hint=keyword_hint,
    )

    started = time.perf_counter()
    parsed = await order_parser.parse(
        original_text=case.text,
        units=units,
        grid_info=ctx["grid_info"],
        game_time=ctx["game_time"],
        issuer_side="blue",
        terrain_context=ctx["terrain_context"],
        contacts_context=ctx["contacts_context"],
        objectives_context=ctx["objectives_context"],
        friendly_status_context=ctx["friendly_status_context"],
        environment_context=ctx["environment_context"],
        orders_context=ctx["orders_context"],
        radio_context=ctx["radio_context"],
        reports_context=ctx["reports_context"],
        map_objects_context=ctx["map_objects_context"],
    )
    elapsed = time.perf_counter() - started

    ok_class = parsed.classification.value == case.expected_classification
    ok_type = case.expected_order_type is None or (
        parsed.order_type is not None and parsed.order_type.value == case.expected_order_type
    )
    return {
        "label": f"{case.label}{repeat_label}",
        "time_s": round(elapsed, 2),
        "system_chars": len(bundle.system),
        "user_chars": len(bundle.user),
        "example_blocks": bundle.user.count("Examples:"),
        "message_blocks": bundle.user.count('MESSAGE: "'),
        "classification": parsed.classification.value,
        "order_type": parsed.order_type.value if parsed.order_type else None,
        "confidence": round(parsed.confidence, 2),
        "passed": ok_class and ok_type,
        "cache_key": bundle.cache_key[:12],
    }


def print_server_info() -> None:
    try:
        with httpx.Client(timeout=5.0) as client:
            health = client.get("http://localhost:8081/health").json()
            slots = client.get("http://localhost:8081/slots").json()
    except Exception as exc:
        print(f"Server info unavailable: {exc}")
        return

    print("Server:")
    print(f"  health={health}")
    if slots:
        slot = slots[0]
        print(f"  slot_ctx={slot.get('n_ctx')} prompt_cache_processing={slot.get('is_processing')}")
        params = slot.get("params", {})
        print(
            "  last_params="
            f"max_tokens={params.get('max_tokens')} temp={params.get('temperature')} "
            f"reasoning_format={params.get('reasoning_format')}"
        )


async def main() -> None:
    print("TDG Order Parser Local Benchmark")
    print("=" * 72)
    print_server_info()

    order_parser._prompt_result_cache.clear()

    cases = [
        BenchCase("cold_breach", "Combat engineers, breach the roadblock at E6-3 and open a lane.", "command", "breach"),
        BenchCase("warm_breach", "Combat engineers, continue to the bridge and keep the lane open.", "command", "breach"),
        BenchCase("warm_fire", "B-squad, request smoke on the crossing and move under concealment.", "command", "request_fire"),
    ]

    results: list[dict] = []
    for case in cases:
        result = await run_case(case)
        results.append(result)
        print(
            f"{result['label']:12s} | {result['time_s']:6.2f}s | "
            f"{result['system_chars']:4d}+{result['user_chars']:4d} chars | "
            f"ex={result['example_blocks']}/msg={result['message_blocks']} | "
            f"{result['classification']:14s} | {str(result['order_type']):12s} | "
            f"conf={result['confidence']:.2f} | {'PASS' if result['passed'] else 'MISS'}"
        )

    duplicate = await run_case(cases[-1], repeat_label="_dup")
    results.append(duplicate)
    print(
        f"{duplicate['label']:12s} | {duplicate['time_s']:6.2f}s | "
        f"{duplicate['system_chars']:4d}+{duplicate['user_chars']:4d} chars | "
        f"ex={duplicate['example_blocks']}/msg={duplicate['message_blocks']} | "
        f"{duplicate['classification']:14s} | {str(duplicate['order_type']):12s} | "
        f"conf={duplicate['confidence']:.2f} | {'PASS' if duplicate['passed'] else 'MISS'}"
    )

    print("-" * 72)
    cold = results[0]["time_s"]
    warm = results[1]["time_s"]
    exact = results[-1]["time_s"]
    print(f"Cold request : {cold:.2f}s")
    print(f"Warm request : {warm:.2f}s")
    print(f"Exact repeat : {exact:.2f}s")
    if warm:
        print(f"Warm speedup : {cold / warm:.2f}x")
    if exact:
        print(f"Repeat speed : {cold / exact:.2f}x vs cold")


if __name__ == "__main__":
    asyncio.run(main())

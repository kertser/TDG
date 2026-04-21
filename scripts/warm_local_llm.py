r"""Warm llama.cpp prompt cache for the most common TDG parser prefixes.

This primes the local server so the first real operator message does not pay the
full cold-prefill cost.

Usage:
    .\venv\Scripts\python.exe scripts\warm_local_llm.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys

from openai import AsyncOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import settings
from backend.services.order_parser import order_parser


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
    ]


def sample_context() -> dict[str, str | dict]:
    return {
        "grid_info": {
            "columns": 10,
            "rows": 10,
            "labeling_scheme": "alphanumeric",
            "height_tops": [
                {"label": "Hill 170", "label_ru": "Высота 170", "snail_path": "E6-3", "elevation_m": 170.0},
            ],
        },
        "game_time": "2026-04-17T12:00:00Z",
        "terrain_context": "Terrain near friendly units:\n  - B-squad: road (E6-3, elev 171m, slope 4.0°)",
        "contacts_context": "Known enemy contacts (1 active):\n  - machine-gun nest at (48.1498, 24.7098), grid E6-2 [conf=90%, src=spotrep]",
        "objectives_context": "Mission: Seize the eastern bridge crossing.",
        "friendly_status_context": "Friendly forces (3 units): avg strength=89%, ammo=85%, morale=91%",
        "environment_context": "Environment: weather=clear, visibility=good",
        "orders_context": "Recent own-side orders (1):\n  - 2026-04-17T11:57:00Z: request_fire -> Mortar Section [validated] | \"Smoke the crossing\"",
        "radio_context": "Recent radio/chat traffic (1 msgs):\n  - 2026-04-17T11:59:00Z [UNIT_RADIO] Mortar Section -> HQ: Ready for smoke mission.",
        "reports_context": "Recent operational reports (1):\n  - 2026-04-17T11:59:30Z [spotrep] from HQ: Enemy machine-gun covers the crossing.",
        "map_objects_context": "Known map objects / points of interest (2):\n  - Eastern bridge (bridge_structure, category=mobility, side=neutral) at (48.1500, 24.7100)\n  - Roadblock Alpha (roadblock, category=obstacle, side=red) at (48.1502, 24.7101)",
    }


async def warm_prompt(client: AsyncOpenAI, text: str) -> None:
    ctx = sample_context()
    keyword_hint = order_parser._fallback_parse(text)
    bundle = order_parser._build_prompt_bundle(
        original_text=text,
        units=sample_units(),
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
    await client.chat.completions.create(
        model=settings.LOCAL_MODEL_NAME,
        messages=[
            {"role": "system", "content": bundle.system},
            {"role": "user", "content": bundle.user},
        ],
        temperature=0.0,
        max_tokens=1,
    )


async def main() -> None:
    if not settings.LOCAL_MODEL_URL:
        raise RuntimeError("LOCAL_MODEL_URL is empty")

    client = AsyncOpenAI(base_url=settings.LOCAL_MODEL_URL, api_key="local")
    prompts = [
        "B-squad, move to E6-3 fast.",
        "B-squad, request smoke on the crossing and move under concealment.",
        "Combat engineers, breach the roadblock at E6-3 and open a lane.",
        "Recon Team, observe the eastern bridge and report all movement.",
    ]
    for prompt in prompts:
        await warm_prompt(client, prompt)
        print(f"Warmed: {prompt}")


if __name__ == "__main__":
    asyncio.run(main())

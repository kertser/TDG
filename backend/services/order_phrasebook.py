from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import tomllib


_PHRASEBOOK_PATH = Path(__file__).resolve().parents[1] / "data" / "order_phrasebook.toml"


def get_order_phrasebook_path() -> Path:
    return _PHRASEBOOK_PATH


@lru_cache(maxsize=1)
def load_order_phrasebook() -> dict:
    with _PHRASEBOOK_PATH.open("rb") as fh:
        return tomllib.load(fh)


def get_order_parser_lexicon() -> dict:
    return load_order_phrasebook()["lexicon"]


def get_order_phrasebook_cases() -> list[dict]:
    return load_order_phrasebook().get("case", [])

from __future__ import annotations

from pathlib import Path
import tomllib


_PHRASEBOOK_PATH = Path(__file__).resolve().parents[1] / "data" / "order_phrasebook.toml"

# Mtime-based hot-reload cache (plan §0.10)
_PHRASEBOOK_CACHE: dict | None = None
_PHRASEBOOK_MTIME: float = 0.0


def get_order_phrasebook_path() -> Path:
    return _PHRASEBOOK_PATH


def get_phrasebook() -> dict:
    """Load phrasebook, auto-reloading if the TOML file changed on disk."""
    global _PHRASEBOOK_CACHE, _PHRASEBOOK_MTIME
    try:
        mtime = _PHRASEBOOK_PATH.stat().st_mtime
    except OSError:
        mtime = 0.0
    if _PHRASEBOOK_CACHE is None or mtime != _PHRASEBOOK_MTIME:
        with _PHRASEBOOK_PATH.open("rb") as fh:
            _PHRASEBOOK_CACHE = tomllib.load(fh)
        _PHRASEBOOK_MTIME = mtime
    return _PHRASEBOOK_CACHE


def reload_phrasebook() -> None:
    """Force-invalidate the phrasebook cache so next call reloads from disk."""
    global _PHRASEBOOK_CACHE
    _PHRASEBOOK_CACHE = None


# Backward-compat aliases used throughout the codebase
def load_order_phrasebook() -> dict:
    return get_phrasebook()


def get_order_parser_lexicon() -> dict:
    return get_phrasebook()["lexicon"]


def get_order_phrasebook_cases() -> list[dict]:
    return get_phrasebook().get("case", [])

"""
Debug logger service — writes detailed tick/engine debug logs to a file.

Controlled via a global toggle (admin panel) and an API endpoint.
Logs are written to `debug_log.txt` in the project root.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Global state ──
_debug_enabled = False
_debug_lock = threading.Lock()
_log_file_path = Path(__file__).resolve().parent.parent.parent / "debug_log.txt"
_log_file = None


def is_debug_logging_enabled() -> bool:
    return _debug_enabled


def enable_debug_logging() -> str:
    """Enable debug logging. Returns the log file path."""
    global _debug_enabled, _log_file
    with _debug_lock:
        _debug_enabled = True
        if _log_file is None:
            try:
                _log_file = open(_log_file_path, "a", encoding="utf-8", buffering=1)
                _log_file.write(f"\n{'='*80}\n")
                _log_file.write(f"Debug logging STARTED at {datetime.now(timezone.utc).isoformat()}\n")
                _log_file.write(f"{'='*80}\n\n")
            except Exception as e:
                logger.error("Failed to open debug log file: %s", e)
                _debug_enabled = False
                return f"ERROR: {e}"
    logger.info("Debug logging enabled → %s", _log_file_path)
    return str(_log_file_path)


def disable_debug_logging() -> None:
    """Disable debug logging and close the file."""
    global _debug_enabled, _log_file
    with _debug_lock:
        _debug_enabled = False
        if _log_file is not None:
            try:
                _log_file.write(f"\n{'='*80}\n")
                _log_file.write(f"Debug logging STOPPED at {datetime.now(timezone.utc).isoformat()}\n")
                _log_file.write(f"{'='*80}\n\n")
                _log_file.close()
            except Exception:
                pass
            _log_file = None
    logger.info("Debug logging disabled")


def dlog(message: str) -> None:
    """Write a debug log message (timestamped) if debug logging is enabled."""
    if not _debug_enabled:
        return
    with _debug_lock:
        if _log_file is not None:
            try:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
                _log_file.write(f"[{ts}] {message}\n")
            except Exception:
                pass


def get_log_contents(tail_lines: int = 200) -> str:
    """Read the last N lines of the debug log file."""
    try:
        if not _log_file_path.exists():
            return "(no debug log file yet)"
        with open(_log_file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return "".join(lines[-tail_lines:])
    except Exception as e:
        return f"Error reading log: {e}"


def clear_log() -> None:
    """Clear the debug log file."""
    global _log_file
    with _debug_lock:
        if _log_file is not None:
            try:
                _log_file.close()
            except Exception:
                pass
            _log_file = None
        try:
            with open(_log_file_path, "w", encoding="utf-8") as f:
                f.write(f"Log cleared at {datetime.now(timezone.utc).isoformat()}\n\n")
            # Re-open if debug is still enabled
            if _debug_enabled:
                _log_file = open(_log_file_path, "a", encoding="utf-8", buffering=1)
        except Exception as e:
            logger.error("Failed to clear debug log: %s", e)



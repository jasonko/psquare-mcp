"""Write-gating and audit logging for admin write tools.

Admin write operations mutate the ParentSquare roster, so they are disabled by
default and only run when ``PS_ENABLE_WRITES`` is set to a truthy value. Every
attempted write is appended to a local JSONL audit log (``PS_AUDIT_LOG``,
default ``~/.parentsquare_audit.log``) with a timestamp, the tool name, its
arguments, and the outcome.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}

WRITES_DISABLED_MESSAGE = (
    "⚠️ ParentSquare write operations are disabled. This is a safety default "
    "because these tools modify the live school roster. To enable them, set the "
    "environment variable PS_ENABLE_WRITES=1 (or true/yes/on) for the MCP server "
    "and restart it."
)


def writes_enabled() -> bool:
    """Return True if ``PS_ENABLE_WRITES`` is set to a truthy value."""
    return os.environ.get("PS_ENABLE_WRITES", "").strip().lower() in _TRUTHY


def _audit_path() -> Path:
    return Path(os.environ.get("PS_AUDIT_LOG", "~/.parentsquare_audit.log")).expanduser()


def audit_write(tool: str, args: dict, ok: bool, detail: str = "") -> None:
    """Append a single JSONL record describing a write attempt.

    Failures to write the audit log are logged but never raised — auditing must
    not break the tool itself.
    """
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args,
        "ok": ok,
        "detail": detail,
    }
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.warning("Failed to write audit log entry (non-fatal)", exc_info=True)

"""Append-only JSONL history writer for consult interactions.

Records every consult.ask() call to a per-task JSONL file at
``{CONSULT_HISTORY_DIR}/{CONSULT_TASK_ID}.jsonl``.  When either env var
is unset the writer is a silent no-op (backward compatible).

Conversation tracking:
  - ``start_new_chat()`` generates a UUID and resets the turn counter.
  - ``record_ask()``    increments the turn and appends one JSON line.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Env vars injected by the supervisor (serial and parallel paths).
# When unset, history writing is disabled.
# ---------------------------------------------------------------------------
_TASK_ID: str = os.environ.get("CONSULT_TASK_ID", "")
_HISTORY_DIR: str = os.environ.get("CONSULT_HISTORY_DIR", "")
_SLOT_ID: str = os.environ.get("CONSULT_SLOT_ID", "")

# ---------------------------------------------------------------------------
# Module-level conversation state
# ---------------------------------------------------------------------------
_chat_id: str = str(uuid.uuid4())  # defensive default
_turn: int = 0


def start_new_chat() -> None:
    """Begin a new conversation: fresh UUID, reset turn counter."""
    global _chat_id, _turn
    _chat_id = str(uuid.uuid4())
    _turn = 0


def _history_file() -> Path | None:
    """Return the per-task JSONL path, or None if history is disabled."""
    if not _TASK_ID or not _HISTORY_DIR:
        return None
    d = Path(_HISTORY_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_TASK_ID}.jsonl"


def record_ask(
    *,
    prompt: str,
    mode: str,
    file_paths: list[str] | None,
    completed: bool,
    response: str,
    downloaded_files: list[str],
    duration_sec: float,
    error: str | None,
) -> None:
    """Append one interaction record to the global archive.

    Never raises — failures are logged and swallowed.
    """
    global _turn
    _turn += 1

    path = _history_file()
    if path is None:
        return

    record: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "task_id": _TASK_ID,
        "chat_id": _chat_id,
        "turn": _turn,
        "slot_id": _SLOT_ID,
        "prompt": prompt,
        "mode": mode,
        "file_paths": file_paths or [],
        "completed": completed,
        "response": response,
        "downloaded_files": downloaded_files,
        "duration_sec": round(duration_sec, 1),
        "error": error,
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to write consult history record")

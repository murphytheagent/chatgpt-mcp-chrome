"""FastMCP server exposing consultation tools via Chrome browser automation."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .browser import BrowserController
from .history import record_ask, start_new_chat
from .models import get_model_config, DEFAULT_MODEL
from .response import ResponseDetector

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

mcp = FastMCP("consult")

_browser = BrowserController()
_detector = ResponseDetector(_browser)
_pending: bool = False
_first_call: bool = True
_PENDING_FILE = os.environ.get("CONSULT_PENDING_FILE", "").strip()
_TASK_ID = os.environ.get("CONSULT_TASK_ID", "").strip()
_SLOT_ID = os.environ.get("CONSULT_SLOT_ID", "").strip()

# Default project — all chats go here unless overridden via env var.
DEFAULT_PROJECT = os.environ.get("CHATGPT_DEFAULT_PROJECT", "Murphy")


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------


def _write_pending_state(
    *,
    phase: str,
    mode: str | None = None,
    file_paths: list[str] | None = None,
) -> None:
    """Write the current consult phase to a supervisor-readable pending file."""
    if not _PENDING_FILE:
        return
    payload = {
        "task_id": _TASK_ID,
        "slot_id": _SLOT_ID,
        "phase": phase,
        "mode": mode or "",
        "file_count": len(file_paths or []),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    path = Path(_PENDING_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        logger.exception("Failed to write consult pending state")


def _clear_pending_state() -> None:
    """Remove the consult pending marker file."""
    if not _PENDING_FILE:
        return
    try:
        Path(_PENDING_FILE).unlink(missing_ok=True)
    except OSError:
        logger.exception("Failed to clear consult pending state")


@mcp.tool()
async def ask(
    prompt: str,
    mode: str | None = None,
    file_paths: list[str] | None = None,
) -> str:
    """Send a prompt to Athena (external expert) and wait for the full response.

    Use this for tasks that need extended reasoning, hard math, proofs,
    or web search — things that benefit from a deeper analysis.

    Args:
        prompt:     The text to send.
        mode:       "deep" (default, Pro + extended thinking) or "standard"
                    (Pro + standard thinking, faster).
        file_paths: Optional list of absolute file paths to attach.

    Returns:
        The assistant's response text (with LaTeX preserved),
        or an error / timeout message.
    """
    global _pending, _first_call

    if _pending:
        return (
            "A previous request is still being processed. "
            "Wait for it to finish before sending another prompt."
        )

    t0 = time.monotonic()
    try:
        _pending = True
        _write_pending_state(phase="navigate_to_project", mode=mode, file_paths=file_paths)

        # Navigate to project folder
        await _browser.navigate_to_project(DEFAULT_PROJECT)

        # Start a fresh conversation on the first call of this MCP session.
        # Tabs persist across dispatches, so without this the new dispatch
        # would append to the previous dispatch's conversation.
        if _first_call:
            _write_pending_state(phase="new_chat", mode=mode, file_paths=file_paths)
            await _browser.new_chat()
            start_new_chat()
            _first_call = False

        _write_pending_state(phase="select_model", mode=mode, file_paths=file_paths)
        model_config = await _browser.select_model(mode)

        # Upload files before sending the message
        if file_paths:
            _write_pending_state(phase="upload_files", mode=mode, file_paths=file_paths)
            await _browser.upload_files(file_paths)

        _write_pending_state(phase="send_message", mode=mode, file_paths=file_paths)
        await _browser.send_message(prompt)
        _write_pending_state(phase="wait_for_response", mode=mode, file_paths=file_paths)
        completed, response = await _detector.wait_for_response(model_config)

        # Try to download any generated files
        _write_pending_state(phase="download_generated_files", mode=mode, file_paths=file_paths)
        downloaded = await _browser.download_generated_files()

        _pending = False

        result = response
        if not completed:
            result = (
                f"Timeout ({model_config.timeout_sec}s) waiting for "
                f"{model_config.display_name}. Partial response:\n\n{response}"
            )

        if downloaded:
            files_str = "\n".join(downloaded)
            result += f"\n\n[Downloaded files]\n{files_str}"

        try:
            record_ask(
                prompt=prompt,
                mode=mode or "deep",
                file_paths=file_paths,
                completed=completed,
                response=response,
                downloaded_files=downloaded,
                duration_sec=time.monotonic() - t0,
                error=None,
            )
        except Exception:
            pass

        return result

    except Exception as exc:
        _pending = False
        logger.exception("consult.ask failed")
        try:
            record_ask(
                prompt=prompt,
                mode=mode or "deep",
                file_paths=file_paths,
                completed=False,
                response="",
                downloaded_files=[],
                duration_sec=time.monotonic() - t0,
                error=str(exc),
            )
        except Exception:
            pass
        return f"Error: {exc}"
    finally:
        _clear_pending_state()


@mcp.tool()
async def new_chat() -> str:
    """Start a new conversation with Athena (external expert)."""
    global _first_call
    if _pending:
        return (
            "A previous request is still being processed. "
            "Wait for it to finish before starting a new chat."
        )
    try:
        await _browser.new_chat()
        start_new_chat()
        _first_call = False
        return "New chat opened."
    except Exception as exc:
        logger.exception("new_chat failed")
        return f"Error: {exc}"


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------


def main() -> None:
    """Run the MCP server on stdio transport."""
    mcp.run(transport="stdio")

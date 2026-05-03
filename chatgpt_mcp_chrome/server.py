"""FastMCP server exposing consultation tools via Chrome browser automation."""

from __future__ import annotations

import logging
import time

from mcp.server.fastmcp import FastMCP

import os

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

# Default project — all chats go here unless overridden via env var.
DEFAULT_PROJECT = os.environ.get("CHATGPT_DEFAULT_PROJECT", "Murphy")


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------


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

        # Navigate to project folder
        await _browser.navigate_to_project(DEFAULT_PROJECT)

        # Start a fresh conversation on the first call of this MCP session.
        # Tabs persist across dispatches, so without this the new dispatch
        # would append to the previous dispatch's conversation.
        if _first_call:
            await _browser.new_chat()
            start_new_chat()
            _first_call = False

        if mode is None:
            # Omitted mode means "use whatever the current browser tab already has
            # selected" — do not touch the model switcher before file upload or send.
            model_config = get_model_config(DEFAULT_MODEL)
            recorded_mode = "current-tab"
        else:
            model_config = await _browser.select_model(mode)
            recorded_mode = mode

        # Upload files before sending the message
        if file_paths:
            await _browser.upload_files(file_paths)

        await _browser.send_message(prompt)
        completed, response = await _detector.wait_for_response(model_config)

        if mode is None:
            current_label = await _browser.describe_current_model()
            if current_label:
                recorded_mode = f"current-tab:{current_label}"

        # Try to download any generated files
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
                mode=recorded_mode,
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
                mode=mode or "current-tab",
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

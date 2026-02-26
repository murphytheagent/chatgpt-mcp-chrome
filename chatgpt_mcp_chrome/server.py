"""FastMCP server exposing consultation tools via Chrome browser automation."""

from __future__ import annotations

import logging

from mcp.server.fastmcp import FastMCP

import os

from .browser import BrowserController
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
    project: str | None = None,
) -> str:
    """Send a prompt to an external reasoning model and wait for the full response.

    Use this for tasks that need extended reasoning, hard math, proofs,
    or web search — things that benefit from a stronger thinking model.

    Args:
        prompt:     The text to send.
        mode:       "standard" (default) or "deep" (extended reasoning,
                    slower but stronger for very hard problems).
        file_paths: Optional list of absolute file paths to attach.
        project:    Project folder for organizing chats (default: "Murphy").

    Returns:
        The assistant's response text (with LaTeX preserved),
        or an error / timeout message.
    """
    global _pending

    if _pending:
        return (
            "A previous request is still being processed. "
            "Wait for it to finish before sending another prompt."
        )

    try:
        _pending = True

        # Navigate to project (default: Murphy)
        target_project = project or DEFAULT_PROJECT
        await _browser.navigate_to_project(target_project)

        model_config = await _browser.select_model(mode)

        # Upload files before sending the message
        if file_paths:
            await _browser.upload_files(file_paths)

        await _browser.send_message(prompt)
        completed, response = await _detector.wait_for_response(model_config)

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

        return result

    except Exception as exc:
        _pending = False
        logger.exception("consult.ask failed")
        return f"Error: {exc}"


@mcp.tool()
async def new_chat() -> str:
    """Start a new conversation with the external reasoning model."""
    if _pending:
        return (
            "A previous request is still being processed. "
            "Wait for it to finish before starting a new chat."
        )
    try:
        await _browser.new_chat()
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

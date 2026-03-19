#!/usr/bin/env python3
"""End-to-end round-trip test for the consult MCP tool.

Sends a trivial prompt to ChatGPT via the browser automation layer and
verifies that a response is detected correctly.  Designed to be run
during developer review to catch UI regressions early.

Usage:
    python3 mcp/chatgpt-mcp-chrome/scripts/test_roundtrip.py

Exit code 0 = pass, 1 = fail.
"""
from __future__ import annotations

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("test_roundtrip")

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from chatgpt_mcp_chrome.browser import BrowserController, SEL_ASSISTANT_MSG
from chatgpt_mcp_chrome.response import ResponseDetector
from chatgpt_mcp_chrome.models import get_model_config

# Short timeout — a trivial question should be answered in <30s.
TIMEOUT_SEC = 90
PROMPT = "What is 2+2? Reply with just the number."


async def main() -> int:
    browser = BrowserController()
    detector = ResponseDetector(browser)

    try:
        page = await browser.ensure_connected()
        logger.info("Connected: %s", page.url)

        await browser.navigate_to_project("Murphy")
        await browser.new_chat()

        model_config = await browser.select_model("standard")
        logger.info("Model: %s", model_config.display_name)

        # Baseline
        msgs = await page.locator(SEL_ASSISTANT_MSG).all()
        logger.info("Baseline assistant messages: %d", len(msgs))

        # Send
        await browser.send_message(PROMPT)
        logger.info("Sent prompt (%d chars), _msg_count_before_send=%d",
                     len(PROMPT), browser._msg_count_before_send)

        # Wait
        completed, response = await detector.wait_for_response(
            model_config, timeout_override=TIMEOUT_SEC,
        )

        # Verify
        msgs_after = await page.locator(SEL_ASSISTANT_MSG).all()

        if completed and response:
            print(f"PASS: Response received in standard mode.")
            print(f"  response: {response[:200]}")
            print(f"  assistant messages: {len(msgs)} -> {len(msgs_after)}")
            return 0
        else:
            print(f"FAIL: Round-trip test failed.")
            print(f"  completed: {completed}")
            print(f"  response length: {len(response)}")
            print(f"  response: '{response[:200]}'")
            print(f"  assistant messages: {len(msgs)} -> {len(msgs_after)}")
            is_gen = await browser.is_generating()
            has_ind = await browser.has_completion_indicators()
            text = await browser.get_last_response()
            print(f"  is_generating: {is_gen}")
            print(f"  has_completion_indicators: {has_ind}")
            print(f"  get_last_response: '{text[:200]}'")
            print(f"\nSee docs/dev/consult-maintenance.md for investigation steps.")
            return 1

    except Exception as exc:
        print(f"FAIL: Exception during round-trip test: {exc}")
        logger.exception("Round-trip test crashed")
        return 1
    finally:
        await browser.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

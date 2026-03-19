#!/usr/bin/env python3
"""Health check: verify ChatGPT DOM selectors still match.

Connects to the automation Chrome via CDP and checks that the key CSS
selectors used by the consult MCP server find elements on an existing
ChatGPT conversation page.  Does NOT send messages or modify state.

Usage:
    python3 mcp/chatgpt-mcp-chrome/scripts/check_selectors.py

Exit code 0 = all critical selectors OK, 1 = one or more broken.
"""
from __future__ import annotations

import asyncio
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from chatgpt_mcp_chrome.browser import (
    SEL_ASSISTANT_MSG,
    SEL_COMPLETION_BUTTONS,
    SEL_PROMPT_TEXTAREA,
    SEL_SEND_BUTTON,
    SEL_STOP_BUTTON,
    SEL_MODEL_SWITCHER,
    SEL_NEW_CHAT,
    SEL_STREAMING,
)

CDP_URL = "http://127.0.0.1:9222"


async def main() -> int:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(CDP_URL)
    except Exception as e:
        print(f"FAIL: Cannot connect to Chrome CDP at {CDP_URL}: {e}")
        await pw.stop()
        return 1

    ctx = browser.contexts[0] if browser.contexts else None
    if not ctx:
        print("FAIL: No browser contexts")
        await pw.stop()
        return 1

    # Find a ChatGPT conversation page with content
    page = None
    for pg in ctx.pages:
        if "chatgpt.com" not in pg.url or "/c/" not in pg.url:
            continue
        count = await pg.locator(SEL_ASSISTANT_MSG).count()
        if count > 0:
            page = pg
            break

    if not page:
        print("SKIP: No ChatGPT conversation page with assistant messages found.")
        print("      Open a ChatGPT conversation in the automation Chrome and retry.")
        await pw.stop()
        return 0  # not a failure, just nothing to check

    print(f"Page: {page.url}")
    failures = []

    # --- Critical selectors (must match) ---
    critical = {
        "SEL_ASSISTANT_MSG": SEL_ASSISTANT_MSG,
        "SEL_PROMPT_TEXTAREA": SEL_PROMPT_TEXTAREA,
        "SEL_MODEL_SWITCHER": SEL_MODEL_SWITCHER,
        ".markdown div": ".markdown",
    }
    for name, sel in critical.items():
        count = await page.locator(sel).count()
        status = "OK" if count > 0 else "BROKEN"
        print(f"  [{status}] {name} ({sel}): count={count}")
        if count == 0:
            failures.append(name)

    # --- Completion buttons (at least one must match) ---
    any_completion = False
    print("  Completion buttons:")
    for sel in SEL_COMPLETION_BUTTONS:
        count = await page.locator(sel).count()
        if count > 0:
            any_completion = True
        print(f"    {'OK' if count > 0 else '--'} {sel}: count={count}")
    if not any_completion:
        print("  [BROKEN] No completion button selector matched!")
        failures.append("SEL_COMPLETION_BUTTONS")
    else:
        print("  [OK] At least one completion button selector matches")

    # --- Informational (not critical) ---
    info = {
        "SEL_SEND_BUTTON": SEL_SEND_BUTTON,
        "SEL_STOP_BUTTON": SEL_STOP_BUTTON,
        "SEL_STREAMING": SEL_STREAMING,
        "SEL_NEW_CHAT": SEL_NEW_CHAT,
    }
    for name, sel in info.items():
        count = await page.locator(sel).count()
        # These may be 0 when not actively generating — just report
        print(f"  [info] {name} ({sel}): count={count}")

    await pw.stop()

    if failures:
        print(f"\nFAIL: {len(failures)} broken selector(s): {', '.join(failures)}")
        print("The ChatGPT UI may have changed. Update browser.py selectors.")
        return 1
    else:
        print("\nPASS: All critical selectors match.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

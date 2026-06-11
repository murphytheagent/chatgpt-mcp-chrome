#!/usr/bin/env python3
"""Read-only diagnostic: dump the live model-switcher + thinking-effort menu.

Connects to the running Chrome (CDP 9222), opens the composer model switcher
on the current page, and prints every menu item's role/testid/aria-checked and
inner_text. If a `pro-thinking-effort` submenu entry exists, it expands it and
dumps the submenu too. Does NOT select anything or send a prompt.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chatgpt_mcp_chrome.browser import BrowserController, SEL_MODEL_MENU_ITEMS  # noqa: E402


async def dump_items(page, label):
    print(f"\n--- {label} ---")
    items = await page.locator(SEL_MODEL_MENU_ITEMS).all()
    print(f"  {len(items)} item(s) match SEL_MODEL_MENU_ITEMS")
    for i, it in enumerate(items):
        try:
            txt = (await it.inner_text(timeout=1000)).strip().replace("\n", " | ")
        except Exception as e:
            txt = f"<inner_text failed: {e}>"
        try:
            role = await it.get_attribute("role")
        except Exception:
            role = None
        try:
            tid = await it.get_attribute("data-testid")
        except Exception:
            tid = None
        try:
            checked = await it.get_attribute("aria-checked")
        except Exception:
            checked = None
        print(f"  [{i}] role={role} testid={tid} checked={checked} text={txt!r}")


async def main() -> int:
    browser = BrowserController()
    page = await browser.ensure_connected()
    print(f"Connected: {page.url}")
    print(f"SEL_MODEL_MENU_ITEMS = {SEL_MODEL_MENU_ITEMS}")

    switcher = await browser._locate_model_switcher(page)
    try:
        print(f"Switcher label: {(await switcher.inner_text()).strip()!r}")
    except Exception:
        pass
    await browser._open_model_switcher_menu(page, switcher)
    await asyncio.sleep(0.5)
    await dump_items(page, "TOP-LEVEL MODEL MENU")

    effort_entry = page.locator('[data-testid*="pro-thinking-effort"]').first
    cnt = await effort_entry.count()
    print(f"\n[data-testid*='pro-thinking-effort'] count = {cnt}")
    if cnt:
        try:
            print(f"  entry text: {(await effort_entry.inner_text()).strip()!r}")
        except Exception:
            pass
        await effort_entry.click(force=True, timeout=3000)
        await asyncio.sleep(0.6)
        await dump_items(page, "EFFORT SUBMENU")

    # Also probe the Pro chip fallback path.
    print("\n[fallback] button:has-text('Pro') count =",
          await page.locator("button:has-text('Pro')").count())

    try:
        await page.keyboard.press("Escape")
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

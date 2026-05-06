"""Unit tests for browser-side model switcher selection helpers."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from chatgpt_mcp_chrome.browser import (
    BrowserController,
    SEL_MODEL_SWITCHER,
    SEL_MODEL_SWITCHER_LEGACY,
)


class _FakeLocator:
    def __init__(
        self,
        *,
        count: int = 1,
        inner_text: str = "",
        aria_label: str = "",
        expanded: str = "false",
        click_fails: bool = False,
        force_click_opens: bool = False,
    ) -> None:
        self._count = count
        self._inner_text = inner_text
        self._aria_label = aria_label
        self._expanded = expanded
        self._click_fails = click_fails
        self._force_click_opens = force_click_opens
        self.first = self

    async def count(self) -> int:
        return self._count

    async def inner_text(self) -> str:
        return self._inner_text

    async def get_attribute(self, name: str) -> str | None:
        if name == "aria-label":
            return self._aria_label
        if name == "aria-expanded":
            return self._expanded
        return None

    async def scroll_into_view_if_needed(self, timeout: int | None = None) -> None:
        return None

    async def click(self, timeout: int | None = None, force: bool = False) -> None:
        if self._click_fails and not force:
            raise RuntimeError("intercepted")
        if force and self._force_click_opens:
            self._expanded = "true"
        elif not self._click_fails:
            self._expanded = "true"

    async def press(self, key: str, timeout: int | None = None) -> None:
        if key in {"Enter", " "}:
            self._expanded = "true"

    async def evaluate(self, script: str) -> None:
        self._expanded = "true"


class _FakePage:
    def __init__(self, mapping: dict[str, _FakeLocator]) -> None:
        self._mapping = mapping
        self.keyboard = SimpleNamespace(press=AsyncMock())

    def locator(self, selector: str) -> _FakeLocator:
        locator = self._mapping.get(selector)
        if locator is None:
            return _FakeLocator(count=0)
        return locator


class TestBrowserModelSwitcher(unittest.IsolatedAsyncioTestCase):
    async def test_locate_model_switcher_prefers_composer_pill(self) -> None:
        controller = BrowserController()
        composer = _FakeLocator(inner_text="Extended Pro")
        legacy = _FakeLocator(aria_label="Model selector, current model is 5.5 Pro")
        page = _FakePage({
            SEL_MODEL_SWITCHER: composer,
            SEL_MODEL_SWITCHER_LEGACY: legacy,
        })

        switcher = await controller._locate_model_switcher(page)

        self.assertIs(switcher, composer)

    async def test_locate_model_switcher_matches_unknown_model_label(self) -> None:
        # Regression: SEL_MODEL_SWITCHER must match on class + aria-haspopup
        # alone, not on a hard-coded list of model names. ChatGPT introduced
        # "Heavy" as a default pill label and the previous text-filtered
        # selector silently skipped it, leaving select_model() to raise
        # "Model switcher control not found" on /project pages.
        controller = BrowserController()
        composer = _FakeLocator(inner_text="Heavy")
        page = _FakePage({SEL_MODEL_SWITCHER: composer})

        switcher = await controller._locate_model_switcher(page)

        self.assertIs(switcher, composer)

    async def test_locate_model_switcher_polls_until_pill_appears(self) -> None:
        # Regression: composer pill is sometimes not hydrated immediately
        # after new_chat() returns, so _locate_model_switcher must poll
        # rather than fail on the first count==0 result.
        controller = BrowserController()
        composer = _FakeLocator(inner_text="Thinking")

        class _DelayedPage(_FakePage):
            def __init__(self, ready_after_calls: int) -> None:
                super().__init__({})
                self._calls = 0
                self._ready_after = ready_after_calls

            def locator(self, selector: str) -> _FakeLocator:
                if selector == SEL_MODEL_SWITCHER:
                    self._calls += 1
                    if self._calls > self._ready_after:
                        return composer
                return _FakeLocator(count=0)

        page = _DelayedPage(ready_after_calls=2)

        switcher = await controller._locate_model_switcher(page, timeout_ms=2_000)

        self.assertIs(switcher, composer)

    async def test_open_model_switcher_menu_uses_force_click_fallback(self) -> None:
        controller = BrowserController()
        switcher = _FakeLocator(
            inner_text="Extended Pro",
            click_fails=True,
            force_click_opens=True,
        )
        page = _FakePage({})

        await controller._open_model_switcher_menu(page, switcher)

        self.assertEqual(await switcher.get_attribute("aria-expanded"), "true")
        page.keyboard.press.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()

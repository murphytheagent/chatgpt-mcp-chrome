"""Unit tests for ResponseDetector (mocked browser)."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from chatgpt_mcp_chrome.models import ModelConfig
from chatgpt_mcp_chrome.response import ResponseDetector

# Use short intervals for tests
import chatgpt_mcp_chrome.response as resp_mod

resp_mod.POLL_INTERVAL_SEC = 0.05
resp_mod.INITIAL_GRACE_SEC = 0.05


def _make_browser(**overrides) -> MagicMock:
    b = MagicMock()
    b.is_generating = overrides.get("is_generating", AsyncMock(return_value=False))
    b.has_completion_indicators = overrides.get(
        "has_completion_indicators", AsyncMock(return_value=False)
    )
    b.get_last_response = overrides.get(
        "get_last_response", AsyncMock(return_value="")
    )
    return b


class TestResponseDetector(unittest.TestCase):
    """Tests for the three-phase completion detection."""

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_completes_via_indicators(self):
        """Phase 2: completion indicators detected immediately."""
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="The answer is 42."),
        )
        det = ResponseDetector(browser)
        ok, text = self._run(det.wait_for_response(ModelConfig("test-id", 10, "Test")))
        self.assertTrue(ok)
        self.assertEqual(text, "The answer is 42.")

    def test_completes_via_stability(self):
        """Phase 3: stability fallback when indicators never appear."""
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(return_value=False),
            get_last_response=AsyncMock(
                side_effect=["partial", "full answer", "full answer", "full answer", "full answer"]
            ),
        )
        det = ResponseDetector(browser)
        ok, text = self._run(det.wait_for_response(ModelConfig("test-id", 10, "Test")))
        self.assertTrue(ok)
        self.assertEqual(text, "full answer")

    def test_waits_while_generating(self):
        """Phase 1: keeps waiting while stop button is visible."""
        gen_sequence = [True, True, False, False, False]
        browser = _make_browser(
            is_generating=AsyncMock(side_effect=gen_sequence),
            has_completion_indicators=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="done"),
        )
        det = ResponseDetector(browser)
        ok, text = self._run(det.wait_for_response(ModelConfig("test-id", 10, "Test")))
        self.assertTrue(ok)
        self.assertEqual(text, "done")
        # is_generating should have been called at least 3 times
        self.assertGreaterEqual(browser.is_generating.call_count, 3)

    def test_timeout_returns_partial(self):
        """Returns partial text and completed=False on timeout."""
        browser = _make_browser(
            is_generating=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="partial..."),
        )
        det = ResponseDetector(browser)
        ok, text = self._run(
            det.wait_for_response(ModelConfig("test-id", 1, "Test"), timeout_override=1)
        )
        self.assertFalse(ok)
        self.assertEqual(text, "partial...")

    def test_empty_response_keeps_polling(self):
        """Keeps polling when no response text is available yet."""
        responses = ["", "", "", "hello"]
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(
                side_effect=[False, False, False, True]
            ),
            get_last_response=AsyncMock(side_effect=responses),
        )
        det = ResponseDetector(browser)
        ok, text = self._run(det.wait_for_response(ModelConfig("test-id", 10, "Test")))
        self.assertTrue(ok)
        self.assertEqual(text, "hello")


if __name__ == "__main__":
    unittest.main()

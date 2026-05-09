"""Unit tests for ResponseDetector (mocked browser)."""

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


class TestResponseDetector(unittest.IsolatedAsyncioTestCase):
    """Tests for the three-phase completion detection.

    Uses ``IsolatedAsyncioTestCase`` so each test gets a fresh event
    loop. The previous ``asyncio.get_event_loop().run_until_complete()``
    pattern fails under Python 3.11+ once any earlier test in the suite
    has closed the default loop (the tests pass in isolation but fail
    when run alongside ``tests/test_browser.py`` / ``tests/test_server.py``).
    """

    async def test_completes_via_indicators(self):
        """Phase 2: completion indicators detected immediately."""
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="The answer is 42."),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(ModelConfig("test-id", 10, "Test"))
        self.assertTrue(ok)
        self.assertEqual(text, "The answer is 42.")

    async def test_completes_via_stability(self):
        """Phase 3: stability fallback when indicators never appear."""
        # STABILITY_CHECKS=5 → after the first "partial" snapshot, need
        # five identical "full answer" snapshots before the detector
        # returns. The first non-matching snapshot resets stable_count,
        # so the iterator below provides one "partial" + six identical
        # snapshots (slightly over what's strictly required, to absorb
        # any minor probe re-orderings).
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(return_value=False),
            get_last_response=AsyncMock(
                side_effect=[
                    "partial",
                    "full answer",
                    "full answer",
                    "full answer",
                    "full answer",
                    "full answer",
                    "full answer",
                ]
            ),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(ModelConfig("test-id", 10, "Test"))
        self.assertTrue(ok)
        self.assertEqual(text, "full answer")

    async def test_waits_while_generating(self):
        """Phase 1: keeps waiting while stop button is visible."""
        gen_sequence = [True, True, False, False, False]
        browser = _make_browser(
            is_generating=AsyncMock(side_effect=gen_sequence),
            has_completion_indicators=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="done"),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(ModelConfig("test-id", 10, "Test"))
        self.assertTrue(ok)
        self.assertEqual(text, "done")
        # is_generating should have been called at least 3 times
        self.assertGreaterEqual(browser.is_generating.call_count, 3)

    async def test_timeout_returns_partial(self):
        """Returns partial text and completed=False on timeout."""
        browser = _make_browser(
            is_generating=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="partial..."),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(
            ModelConfig("test-id", 1, "Test"), timeout_override=1
        )
        self.assertFalse(ok)
        self.assertEqual(text, "partial...")

    async def test_empty_response_keeps_polling(self):
        """Keeps polling when no response text is available yet."""
        # On the poll that produces "hello", `has_completion_indicators`
        # returns True, but "hello" is shorter than
        # SHORT_RESPONSE_THRESHOLD, so the detector triggers an extra
        # `get_last_response()` re-check before returning. Provide one
        # additional matching snapshot so the re-check sees stable text.
        responses = ["", "", "", "hello", "hello"]
        browser = _make_browser(
            is_generating=AsyncMock(return_value=False),
            has_completion_indicators=AsyncMock(
                side_effect=[False, False, False, True]
            ),
            get_last_response=AsyncMock(side_effect=responses),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(ModelConfig("test-id", 10, "Test"))
        self.assertTrue(ok)
        self.assertEqual(text, "hello")

    async def test_pro_thinking_blocks_until_done(self):
        """Phase 1 blocks while is_generating detects Pro thinking."""
        # Simulate: 5 polls of "generating" (Pro thinking), then done.
        # When generation stops, the response ("Full proof here...") is
        # under SHORT_RESPONSE_THRESHOLD, so the detector calls
        # `is_generating` once more during the short-response re-check.
        # That trailing False keeps the side-effect iterator from
        # exhausting on Python 3.11+ (StopAsyncIteration → test failure).
        gen_seq = [True, True, True, True, True, False, False]
        browser = _make_browser(
            is_generating=AsyncMock(side_effect=gen_seq),
            has_completion_indicators=AsyncMock(return_value=True),
            get_last_response=AsyncMock(return_value="Full proof here..."),
        )
        det = ResponseDetector(browser)
        ok, text = await det.wait_for_response(ModelConfig("test-id", 30, "Test"))
        self.assertTrue(ok)
        self.assertEqual(text, "Full proof here...")
        # Should have polled is_generating at least 6 times
        self.assertGreaterEqual(browser.is_generating.call_count, 6)


if __name__ == "__main__":
    unittest.main()

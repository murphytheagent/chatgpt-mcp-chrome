"""Unit tests for consult.ask mode-selection behavior."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import chatgpt_mcp_chrome.server as server_mod


class TestAskModeBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_mode_omitted_skips_model_switch_and_records_current_tab(self) -> None:
        browser = MagicMock()
        browser.navigate_to_project = AsyncMock()
        browser.new_chat = AsyncMock()
        browser.select_model = AsyncMock(side_effect=AssertionError("should not switch"))
        browser.upload_files = AsyncMock()
        browser.send_message = AsyncMock()
        browser.download_generated_files = AsyncMock(return_value=[])
        browser.describe_current_model = AsyncMock(return_value="Extended Pro")

        detector = MagicMock()
        detector.wait_for_response = AsyncMock(return_value=(True, "ok"))

        with (
            patch.object(server_mod, "_browser", browser),
            patch.object(server_mod, "_detector", detector),
            patch.object(server_mod, "record_ask") as record_ask,
            patch.object(server_mod, "start_new_chat"),
        ):
            server_mod._pending = False
            server_mod._first_call = False
            result = await server_mod.ask("hello", mode=None, file_paths=["/tmp/a"])

        self.assertEqual(result, "ok")
        browser.select_model.assert_not_called()
        browser.upload_files.assert_awaited_once_with(["/tmp/a"])
        browser.send_message.assert_awaited_once_with("hello")
        record_ask.assert_called_once()
        self.assertEqual(record_ask.call_args.kwargs["mode"], "current-tab:Extended Pro")

    async def test_explicit_mode_failure_stops_before_upload(self) -> None:
        browser = MagicMock()
        browser.navigate_to_project = AsyncMock()
        browser.new_chat = AsyncMock()
        browser.select_model = AsyncMock(
            side_effect=RuntimeError("Mode switching failed for requested mode 'deep': broken")
        )
        browser.upload_files = AsyncMock()
        browser.send_message = AsyncMock()
        browser.download_generated_files = AsyncMock(return_value=[])

        detector = MagicMock()
        detector.wait_for_response = AsyncMock(return_value=(True, "ok"))

        with (
            patch.object(server_mod, "_browser", browser),
            patch.object(server_mod, "_detector", detector),
            patch.object(server_mod, "record_ask") as record_ask,
            patch.object(server_mod, "start_new_chat"),
        ):
            server_mod._pending = False
            server_mod._first_call = False
            result = await server_mod.ask("hello", mode="deep", file_paths=["/tmp/a"])

        self.assertIn("Error: Mode switching failed for requested mode 'deep'", result)
        browser.upload_files.assert_not_called()
        browser.send_message.assert_not_called()
        record_ask.assert_called_once()
        self.assertEqual(record_ask.call_args.kwargs["mode"], "deep")


if __name__ == "__main__":
    unittest.main()

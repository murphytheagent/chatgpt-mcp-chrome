"""Response completion detection for ChatGPT."""

from __future__ import annotations

import asyncio
import logging
import time

from .browser import BrowserController
from .models import ModelConfig

logger = logging.getLogger(__name__)

# Tuning knobs
POLL_INTERVAL_SEC = 2.0
STABILITY_CHECKS = 3  # consecutive identical non-empty snapshots → done
INITIAL_GRACE_SEC = 3.0  # let the UI start generating before first poll
# Short responses that appear alongside completion indicators may be progress
# placeholders ("I'm checking...", "I'll present...").  Require a stabilization
# re-check when the detected response is below this character threshold.
SHORT_RESPONSE_THRESHOLD = 200


class ResponseDetector:
    """Detects when ChatGPT has finished generating a response.

    Three-phase approach:
    1. **Generation guard** — while streaming class is present, stop button
       visible, or no response text yet, the model is still working.
    2. **Completion indicators + text** — once generation stops, require both
       action buttons (Copy / Good response) AND non-empty response text.
    3. **Stability fallback** — if indicators never appear, fall back to
       content-stability polling: N consecutive identical non-empty text
       snapshots means the response has settled.
    """

    def __init__(self, browser: BrowserController) -> None:
        self._browser = browser

    async def wait_for_response(
        self,
        model_config: ModelConfig,
        timeout_override: int | None = None,
    ) -> tuple[bool, str]:
        """Block until ChatGPT finishes generating.

        Returns ``(completed, response_text)``.
        *completed* is ``False`` when the timeout is reached.
        """
        timeout = timeout_override or model_config.timeout_sec
        deadline = time.monotonic() + timeout

        # Phase 0: brief grace period for the UI to react
        await asyncio.sleep(INITIAL_GRACE_SEC)

        last_text = ""
        stable_count = 0

        while time.monotonic() < deadline:
            # Phase 1: generation guard
            if await self._browser.is_generating():
                stable_count = 0
                last_text = ""
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # Phase 2: completion indicators + non-empty text
            response = await self._browser.get_last_response()
            if await self._browser.has_completion_indicators() and response:
                if len(response) < SHORT_RESPONSE_THRESHOLD:
                    # Short text with indicators may be a progress placeholder.
                    # Wait one poll and re-check; if the text changed, the real
                    # response is still arriving.
                    await asyncio.sleep(POLL_INTERVAL_SEC)
                    recheck = await self._browser.get_last_response()
                    if recheck != response:
                        logger.info(
                            "Short response changed after re-check "
                            "(%d→%d chars), continuing",
                            len(response),
                            len(recheck),
                        )
                        stable_count = 0
                        last_text = recheck or ""
                        continue
                logger.info("Response complete (indicators + text)")
                return True, response

            # Phase 3: stability fallback
            if not response:
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            if response == last_text:
                stable_count += 1
                if stable_count >= STABILITY_CHECKS:
                    logger.info(
                        "Response complete (stable for %d polls)", stable_count
                    )
                    return True, response
            else:
                stable_count = 1
                last_text = response

            await asyncio.sleep(POLL_INTERVAL_SEC)

        # Timeout — return whatever we have
        final_text = await self._browser.get_last_response()
        logger.warning("Timed out after %ds waiting for response", timeout)
        return False, final_text

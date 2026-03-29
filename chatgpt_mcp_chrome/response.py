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
STABILITY_CHECKS = 5  # consecutive identical non-empty snapshots → done
SHORT_RESPONSE_THRESHOLD = 500  # chars; short responses get extra verification
INITIAL_GRACE_SEC = 3.0  # let the UI start generating before first poll
DIAG_LOG_INTERVAL = 15  # log diagnostics every N polls (~30s at 2s interval)


class ResponseDetector:
    """Detects when ChatGPT has finished generating a response.

    Three-phase approach:
    1. **Generation guard** — while the stop button is visible, streaming
       class is present, "Pro thinking" shimmer is active, or no response
       text has appeared yet, the model is still working.
    2. **Completion indicators + text** — once generation stops, require
       both action buttons (Copy / Good response) AND non-empty response.
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
        start = time.monotonic()

        # Phase 0: brief grace period for the UI to react
        await asyncio.sleep(INITIAL_GRACE_SEC)

        last_text = ""
        stable_count = 0
        poll_count = 0

        while time.monotonic() < deadline:
            poll_count += 1

            # Phase 1: generation guard
            if await self._browser.is_generating():
                stable_count = 0
                last_text = ""
                if poll_count % DIAG_LOG_INTERVAL == 0:
                    elapsed = int(time.monotonic() - start)
                    logger.info(
                        "Waiting: phase=generating, elapsed=%ds, polls=%d",
                        elapsed, poll_count,
                    )
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            # Phase 2: completion indicators + non-empty text
            response = await self._browser.get_last_response()
            if await self._browser.has_completion_indicators() and response:
                # Short-response re-check: placeholder texts like
                # "I'm checking..." can briefly show completion indicators
                # before the model starts a new assistant message.
                if len(response) < SHORT_RESPONSE_THRESHOLD:
                    logger.info(
                        "Short response (%d chars) with indicators — "
                        "re-checking after 2 poll intervals",
                        len(response),
                    )
                    await asyncio.sleep(POLL_INTERVAL_SEC * 2)

                    recheck = await self._browser.get_last_response()
                    still_generating = await self._browser.is_generating()

                    if recheck != response or still_generating:
                        # Text changed or model resumed generating —
                        # reset stability and keep polling.
                        logger.info(
                            "Short-response re-check: changed=%s, "
                            "is_generating=%s — continuing poll",
                            recheck != response, still_generating,
                        )
                        stable_count = 0
                        last_text = recheck or ""
                        continue

                elapsed = int(time.monotonic() - start)
                logger.info(
                    "Response complete (indicators + text) after %ds, "
                    "text_len=%d", elapsed, len(response),
                )
                return True, response

            # Phase 3: stability fallback
            if not response:
                if poll_count % DIAG_LOG_INTERVAL == 0:
                    elapsed = int(time.monotonic() - start)
                    logger.info(
                        "Waiting: phase=no_text, elapsed=%ds, polls=%d",
                        elapsed, poll_count,
                    )
                await asyncio.sleep(POLL_INTERVAL_SEC)
                continue

            if response == last_text:
                stable_count += 1
                if stable_count >= STABILITY_CHECKS:
                    elapsed = int(time.monotonic() - start)
                    logger.info(
                        "Response complete (stable for %d polls) after %ds, "
                        "text_len=%d", stable_count, elapsed, len(response),
                    )
                    return True, response
            else:
                stable_count = 1
                last_text = response

            if poll_count % DIAG_LOG_INTERVAL == 0:
                elapsed = int(time.monotonic() - start)
                logger.info(
                    "Waiting: phase=stability, elapsed=%ds, polls=%d, "
                    "stable=%d/%d, text_len=%d",
                    elapsed, poll_count, stable_count, STABILITY_CHECKS,
                    len(response),
                )

            await asyncio.sleep(POLL_INTERVAL_SEC)

        # Timeout — log diagnostics and return whatever we have
        final_text = await self._browser.get_last_response()
        elapsed = int(time.monotonic() - start)
        is_gen = await self._browser.is_generating()
        has_ind = await self._browser.has_completion_indicators()
        logger.warning(
            "Timed out after %ds (%d polls). is_generating=%s, "
            "has_indicators=%s, text_len=%d",
            elapsed, poll_count, is_gen, has_ind, len(final_text),
        )
        return False, final_text

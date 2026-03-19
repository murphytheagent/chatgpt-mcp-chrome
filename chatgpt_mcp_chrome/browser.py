"""Playwright CDP connection to Chrome and ChatGPT page interactions."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    async_playwright,
)

from .models import ModelConfig, get_model_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CSS selectors — centralised so UI changes only need one update
# ---------------------------------------------------------------------------
SEL_PROMPT_TEXTAREA = "#prompt-textarea"
SEL_SEND_BUTTON = 'button[data-testid="send-button"]'
SEL_ASSISTANT_MSG = 'div[data-message-author-role="assistant"]'
SEL_STOP_BUTTON = 'button[data-testid="stop-button"]'
# Legacy selectors kept as fallback for older ChatGPT UI versions
SEL_STOP_LEGACY_GENERATING = 'button[aria-label="Stop generating"]'
SEL_STOP_LEGACY_REASONING = 'button[aria-label="Stop reasoning"]'
SEL_STREAMING = ".result-streaming"
# Completion-indicator selectors (page-level, NOT scoped to a container).
# On project pages the action buttons live outside the assistant message div.
SEL_COMPLETION_BUTTONS = [
    'button[data-testid="copy-turn-action-button"]',
    'button[data-testid="good-response-turn-action-button"]',
    'button[aria-label="Copy response"]',
    'button[aria-label="Good response"]',
]
SEL_NEW_CHAT = '[data-testid="create-new-chat-button"]'
SEL_MODEL_SWITCHER = '[data-testid="model-switcher-dropdown-button"]'
SEL_FILE_INPUT = 'input[type="file"]'
SEL_ATTACH_BUTTON = 'button[aria-label="Attach files"]'

CDP_URL = os.environ.get("CHATGPT_CDP_URL", "http://127.0.0.1:9222")
# Fixed slot ID for tab isolation in multi-agent setups.  Each slot
# gets a persistent tab identified by window.name ("consult-slot-N").
# When unset, falls back to a single shared tab (serial mode).
SLOT_ID = os.environ.get("CONSULT_SLOT_ID", "")
CHROME_PATH = os.environ.get(
    "CHROME_PATH",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
CHROME_USER_DATA_DIR = os.environ.get(
    "CHROME_USER_DATA_DIR",
    os.path.expanduser("~/Library/Application Support/Google/Chrome-Automation"),
)

# Transient text that should NOT be treated as a real response
TRANSIENT_TEXTS = frozenset({
    "pro thinking",
    "thinking",
    "thinking…",
    "thinking...",
    "analyzing",
    "analyzing…",
    "analyzing...",
    "searching",
    "searching…",
    "searching...",
    "chatgpt said:",
    "",
})


class BrowserController:
    """Manages a persistent CDP connection to Chrome and ChatGPT page actions."""

    def __init__(self, slot_id: str = "") -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._msg_count_before_send: int = 0
        self._slot_id: str = slot_id or SLOT_ID

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def ensure_connected(self) -> Page:
        """Return the active ChatGPT page, (re)connecting as needed."""
        if self._page is not None and not self._page.is_closed():
            return self._page

        self._page = None
        self._browser = None

        if self._playwright is None:
            self._playwright = await async_playwright().start()

        try:
            await self._connect_cdp()
        except Exception:
            logger.info("CDP connection failed — launching Chrome")
            self._launch_chrome()
            for _ in range(15):
                await asyncio.sleep(1)
                try:
                    await self._connect_cdp()
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(
                    f"Could not connect to Chrome on {CDP_URL} after launch. "
                    "Is Chrome installed and running with --remote-debugging-port?"
                )

        return self._page  # type: ignore[return-value]

    async def _connect_cdp(self) -> None:
        self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)  # type: ignore[union-attr]
        contexts = self._browser.contexts
        if not contexts:
            raise RuntimeError("No browser contexts found.")

        ctx = contexts[0]
        slot_name = f"consult-slot-{self._slot_id}" if self._slot_id else ""

        # Find an existing tab by window.name (survives page navigations).
        # Parallel mode: match "consult-slot-N" exactly.
        # Serial mode: match any ChatGPT tab whose window.name is NOT a slot tag.
        for pg in ctx.pages:
            if "chatgpt.com" not in pg.url:
                continue
            try:
                wname = await pg.evaluate("window.name")
            except Exception:
                continue
            if slot_name and wname == slot_name:
                self._page = pg
                logger.info("Reusing slot %s tab: %s", self._slot_id, pg.url)
                return
            if not slot_name and not (wname or "").startswith("consult-slot-"):
                self._page = pg
                logger.info("Reusing existing ChatGPT tab: %s", pg.url)
                return

        # No matching tab found — create a new one.
        self._page = await ctx.new_page()
        await self._page.goto(
            "https://chatgpt.com/", wait_until="domcontentloaded", timeout=30_000
        )
        await self._page.wait_for_selector(SEL_PROMPT_TEXTAREA, timeout=15_000)
        if slot_name:
            await self._page.evaluate(f'window.name = "{slot_name}"')
        logger.info("Opened ChatGPT tab%s", f" for slot {self._slot_id}" if self._slot_id else "")

    def _launch_chrome(self) -> None:
        cmd = [
            CHROME_PATH,
            f"--remote-debugging-port={CDP_URL.rsplit(':', 1)[-1]}",
            f"--user-data-dir={CHROME_USER_DATA_DIR}",
            "--no-first-run",
            "--no-default-browser-check",
            "https://chatgpt.com/",
        ]
        logger.info("Launching Chrome: %s", " ".join(cmd))
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    async def close(self) -> None:
        """Disconnect from Chrome (does **not** close the browser or the tab).

        Slot tabs are left open so the next dispatch for the same slot can
        reconnect to them, preserving ChatGPT conversation context.
        """
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    async def select_model(self, model: str | None) -> ModelConfig:
        """Select a model and thinking effort via the dropdown menu.

        Opens the model-switcher dropdown, clicks the target menu item by
        its ``data-testid``, then sets the Pro thinking effort if applicable.
        """
        page = await self.ensure_connected()
        config = get_model_config(model)

        # Check if the target model is already selected
        switcher = page.locator(SEL_MODEL_SWITCHER).first
        need_model_switch = True
        try:
            label = await switcher.get_attribute("aria-label") or ""
            # e.g. "Model selector, current model is 5.2 Pro"
            if "pro" in label.lower():
                need_model_switch = False
        except Exception:
            pass

        if need_model_switch:
            # Open the dropdown
            await switcher.click(timeout=5_000)
            await asyncio.sleep(0.5)

            # Click the target menu item
            item = page.locator(f'[data-testid="{config.dropdown_testid}"]').first
            await item.click(timeout=5_000)
            await asyncio.sleep(1)

        # Set thinking effort via the Pro chip menu
        if config.thinking_effort:
            await self._set_thinking_effort(config.thinking_effort)

        logger.info("Selected mode '%s' (effort: %s)", config.display_name, config.thinking_effort)
        return config

    async def _set_thinking_effort(self, effort: str) -> None:
        """Set Pro thinking effort ('Standard' or 'Extended').

        Clicks the Pro chip in the composer footer to open the effort menu,
        then selects the target option via role=menuitemradio text match.
        """
        page = await self.ensure_connected()

        # Find the Pro chip button (the one with text "Pro", not the X button)
        pro_btn = page.locator('button:has-text("Pro")').last
        bbox = await pro_btn.bounding_box()
        if not bbox:
            logger.warning("Pro chip not found — skipping effort selection")
            return

        # Click the right side of the chip to open the effort menu
        await page.mouse.click(
            bbox["x"] + bbox["width"] - 5,
            bbox["y"] + bbox["height"] / 2,
        )
        await asyncio.sleep(0.8)

        # Check if the desired effort is already selected
        target = page.locator(f'[role="menuitemradio"]:has-text("{effort}")')
        try:
            checked = await target.get_attribute("aria-checked", timeout=3_000)
            if checked == "true":
                # Already set — dismiss menu by pressing Escape
                await page.keyboard.press("Escape")
                await asyncio.sleep(0.3)
                logger.info("Thinking effort '%s' already selected", effort)
                return
            await target.click(timeout=3_000)
            await asyncio.sleep(0.5)
            logger.info("Set thinking effort to '%s'", effort)
        except Exception:
            # Menu might not have appeared (e.g. non-Pro model) — dismiss
            await page.keyboard.press("Escape")
            logger.warning("Could not set thinking effort to '%s'", effort)

    # ------------------------------------------------------------------
    # Project navigation
    # ------------------------------------------------------------------

    async def navigate_to_project(self, project_name: str) -> None:
        """Navigate into a ChatGPT Project by name.

        Finds the project URL from the sidebar (via JS to avoid overlay
        pointer-event issues) and navigates directly.  If already in the
        target project, this is a no-op.
        """
        page = await self.ensure_connected()

        # Already in this project?
        if "/g/" in page.url and project_name.lower() in page.url.lower():
            logger.info("Already in project '%s': %s", project_name, page.url)
            return

        # Find the project href via JS (sidebar links may be behind overlays)
        href = await page.evaluate("""(name) => {
            const links = document.querySelectorAll('a[href*="/g/"]');
            for (const a of links) {
                if (a.textContent.trim().toLowerCase() === name.toLowerCase()) {
                    return a.getAttribute('href');
                }
            }
            return null;
        }""", project_name)

        if not href:
            logger.warning("Project '%s' not found in sidebar", project_name)
            return

        url = f"https://chatgpt.com{href}" if href.startswith("/") else href
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_selector(SEL_PROMPT_TEXTAREA, timeout=10_000)
        self._msg_count_before_send = 0
        logger.info("Navigated to project '%s': %s", project_name, page.url)

    # ------------------------------------------------------------------
    # File upload
    # ------------------------------------------------------------------

    async def upload_files(self, file_paths: list[str]) -> None:
        """Attach files to the current chat message via the hidden file input."""
        page = await self.ensure_connected()

        # ChatGPT has a hidden <input type="file"> — set files on it directly
        file_input = page.locator(SEL_FILE_INPUT).first
        try:
            await file_input.set_input_files(file_paths, timeout=5_000)
        except Exception:
            # Fallback: click the attach button first to ensure the input exists
            try:
                attach_btn = page.locator(SEL_ATTACH_BUTTON).first
                await attach_btn.click(timeout=3_000)
                await asyncio.sleep(1)
                file_input = page.locator(SEL_FILE_INPUT).first
                await file_input.set_input_files(file_paths, timeout=5_000)
            except Exception as exc:
                # Last resort: try the + button which also reveals file input
                plus_btn = page.locator('button[aria-label="Add content"]').first
                await plus_btn.click(timeout=3_000)
                await asyncio.sleep(1)
                upload_opt = page.locator('text="Upload from computer"').first
                await upload_opt.click(timeout=3_000)
                await asyncio.sleep(1)
                file_input = page.locator(SEL_FILE_INPUT).first
                await file_input.set_input_files(file_paths, timeout=5_000)

        # Wait for upload processing
        await asyncio.sleep(2)
        logger.info("Uploaded %d file(s)", len(file_paths))

    # ------------------------------------------------------------------
    # Chat actions
    # ------------------------------------------------------------------

    async def send_message(self, prompt: str) -> None:
        """Type *prompt* into the chat textarea and send it."""
        page = await self.ensure_connected()

        # Record assistant message count BEFORE sending so we can detect a new response
        msgs = await page.locator(SEL_ASSISTANT_MSG).all()
        self._msg_count_before_send = len(msgs)

        # Remember if we're on a project page — successful send navigates to /c/
        on_project_page = page.url.endswith("/project")

        textarea = page.locator(SEL_PROMPT_TEXTAREA)
        await textarea.wait_for(state="visible", timeout=10_000)
        await textarea.click()
        await textarea.fill(prompt)

        # Playwright's fill() on contenteditable may not trigger React's
        # synthetic onChange in all ChatGPT page variants (e.g. the project
        # overview page).  Dispatch native input events so React picks up the
        # new text and enables the send button.
        await textarea.evaluate("""el => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }""")
        await asyncio.sleep(0.5)

        send_btn = page.locator(SEL_SEND_BUTTON).first
        try:
            await send_btn.click(timeout=3_000)
        except Exception:
            await textarea.press("Enter")
        logger.info("Message sent (%d chars)", len(prompt))

        # On project pages, verify the send actually worked by checking for
        # navigation to a conversation URL (/c/).  If it didn't navigate,
        # retry with progressively more aggressive strategies.
        if on_project_page:
            await self._verify_project_page_send(page, prompt)

    async def _verify_project_page_send(
        self, page: Page, prompt: str = "",
    ) -> None:
        """After sending from a /project page, verify the message was delivered.

        When a message is successfully submitted from the project overview
        page, ChatGPT navigates to ``/c/{conversation_id}``.  If this
        doesn't happen within a grace period, try progressively more
        aggressive send strategies.  Also detect alternative success signals
        (textarea emptied, article count increased) that indicate the
        message was delivered even without a URL change.
        """

        async def _check_success() -> str | None:
            """Return a reason string if delivery can be confirmed, else None."""
            if "/c/" in page.url:
                return f"URL navigated: {page.url}"
            # Textarea cleared = ChatGPT consumed the message
            try:
                txt = await page.locator(SEL_PROMPT_TEXTAREA).inner_text(
                    timeout=1_000
                )
                textarea_empty = not txt.strip()
            except Exception:
                textarea_empty = False
            msgs = await page.locator(SEL_ASSISTANT_MSG).all()
            new_msg = len(msgs) > self._msg_count_before_send
            if textarea_empty and new_msg:
                return "textarea empty + new assistant message appeared"
            if new_msg:
                return "new assistant message appeared"
            return None

        # --- Attempt 1: wait up to 10s for any success signal ---------------
        for _ in range(10):
            await asyncio.sleep(1)
            reason = await _check_success()
            if reason:
                self._msg_count_before_send = 0
                logger.info("Project-page send verified (%s)", reason)
                return

        logger.warning(
            "Project-page send: no success signal after 10s, retrying"
        )

        # --- Attempt 2: re-trigger send via Enter key -----------------------
        textarea = page.locator(SEL_PROMPT_TEXTAREA)
        try:
            await textarea.click(timeout=3_000)
            await textarea.press("Enter")
        except Exception:
            pass

        for _ in range(8):
            await asyncio.sleep(1)
            reason = await _check_success()
            if reason:
                self._msg_count_before_send = 0
                logger.info(
                    "Project-page send verified on Enter retry (%s)", reason
                )
                return

        logger.warning(
            "Project-page send: Enter retry failed, trying keyboard type"
        )

        # --- Attempt 3: clear, re-type via keyboard, send -------------------
        # fill() may not trigger React state on some page variants.
        # Keyboard type is slower but fires real key events.
        try:
            await textarea.click(timeout=3_000)
            # Select all + delete to clear
            modifier = "Meta" if os.uname().sysname == "Darwin" else "Control"
            await page.keyboard.press(f"{modifier}+a")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(0.3)
            # Type a truncated version if prompt is huge (keyboard.type is slow)
            text_to_type = prompt if len(prompt) <= 2000 else prompt[:2000]
            await page.keyboard.type(text_to_type, delay=2)
            await asyncio.sleep(0.5)
            # Try send button, then Enter
            send_btn = page.locator(SEL_SEND_BUTTON).first
            try:
                await send_btn.click(timeout=3_000)
            except Exception:
                await page.keyboard.press("Enter")
        except Exception as exc:
            logger.warning("keyboard-type send failed: %s", exc)

        for _ in range(10):
            await asyncio.sleep(1)
            reason = await _check_success()
            if reason:
                self._msg_count_before_send = 0
                logger.info(
                    "Project-page send verified on keyboard retry (%s)",
                    reason,
                )
                return

        raise RuntimeError(
            "Send from project page failed: no delivery signal after three "
            "strategies (~30s total). The message was not delivered."
        )

    async def get_last_response(self) -> str:
        """Extract the text of the last assistant message.

        Returns empty string if no new response has appeared since the last
        ``send_message`` call, or if the model is still in a thinking phase
        (Pro/Thinking modes show a thinking indicator *before* the actual
        response renders inside a ``.markdown`` div).

        KaTeX-rendered math is converted back to LaTeX notation by extracting
        the raw TeX from ``<annotation>`` elements inside ``.katex`` spans.
        """
        page = await self.ensure_connected()

        msgs = await page.locator(SEL_ASSISTANT_MSG).all()
        if not msgs:
            return ""

        # Only consider messages that appeared AFTER we sent the message
        if len(msgs) <= self._msg_count_before_send:
            return ""

        last_msg = msgs[-1]

        # The .markdown div is the authoritative signal: it only appears once
        # the actual response text starts rendering.  During Pro/Thinking
        # "thinking" phases the message div exists but contains only a
        # thinking indicator — no .markdown div.
        #
        # ChatGPT Pro renders thinking summaries in a *first* .markdown div
        # and the actual response in a *second* .markdown div within the
        # same message.  Always use the LAST .markdown to get the real
        # response, not the thinking summary.
        md_all = await last_msg.locator(".markdown").all()
        if md_all:
            md = md_all[-1]  # last .markdown = actual response
            try:
                text = await self._extract_text_with_latex(md)
                if text and text.lower() not in TRANSIENT_TEXTS:
                    return text
            except Exception:
                pass

        # Fallback: .prose div (some response formats)
        prose = last_msg.locator(".prose").first
        try:
            if await prose.count():
                text = (await prose.inner_text()).strip()
                if text and text.lower() not in TRANSIENT_TEXTS:
                    return text
        except Exception:
            pass

        # No .markdown or .prose → still thinking / not ready
        return ""

    async def _extract_text_with_latex(self, element) -> str:
        """Extract text from a DOM element, converting KaTeX back to LaTeX.

        Clones the element in JS, replaces each ``.katex`` span with its
        ``<annotation encoding="application/x-tex">`` content wrapped in
        ``$...$`` (inline) or ``$$...$$`` (display), then returns innerText.
        """
        text = await element.evaluate("""el => {
            const clone = el.cloneNode(true);

            // Replace display math (.katex-display) first
            clone.querySelectorAll('.katex-display').forEach(kd => {
                const ann = kd.querySelector('annotation[encoding="application/x-tex"]');
                if (ann) {
                    kd.replaceWith('$$' + ann.textContent + '$$');
                }
            });

            // Replace remaining inline math (.katex)
            clone.querySelectorAll('.katex').forEach(k => {
                const ann = k.querySelector('annotation[encoding="application/x-tex"]');
                if (ann) {
                    k.replaceWith('$' + ann.textContent + '$');
                }
            });

            return clone.innerText;
        }""")
        return (text or "").strip()

    async def download_generated_files(self, save_dir: str = "/tmp") -> list[str]:
        """Click download links in the last response and save the files.

        ChatGPT generates files in a sandbox and presents them as either:
        - ``<a class="cursor-pointer">`` links (no ``href``), or
        - ``<button class="behavior-btn ...">`` buttons with "Download" text.

        This method clicks each one and uses Playwright's download API to
        capture the file.

        Returns a list of saved file paths.
        """
        page = await self.ensure_connected()

        msgs = await page.locator(SEL_ASSISTANT_MSG).all()
        if not msgs:
            return []

        last = msgs[-1]

        # Strategy 1: <a class="cursor-pointer"> inside .markdown
        md = last.locator(".markdown").first
        clickables = []
        if await md.count():
            clickables = await md.locator("a.cursor-pointer").all()
            if not clickables:
                clickables = await md.locator("a:not([href])").all()

        # Strategy 2: <button class="behavior-btn"> with "Download" text
        if not clickables:
            btns = await last.locator("button.behavior-btn").all()
            for btn in btns:
                text = (await btn.inner_text()).strip().lower()
                if "download" in text:
                    clickables.append(btn)

        saved: list[str] = []
        for el in clickables:
            text = (await el.inner_text()).strip()
            if not text:
                continue
            try:
                async with page.expect_download(timeout=15_000) as dl_info:
                    await el.click()
                download = await dl_info.value
                dest = os.path.join(save_dir, download.suggested_filename)
                await download.save_as(dest)
                saved.append(dest)
                logger.info("Downloaded: %s", dest)
            except Exception as exc:
                logger.warning("Download failed for '%s': %s", text, exc)

        return saved

    async def is_generating(self) -> bool:
        """Return *True* while ChatGPT is actively generating or thinking.

        Defaults to *True* when all visibility checks fail with exceptions
        (e.g. transient CDP/Playwright glitches).  A false negative is far
        more costly than a false positive: it can cause the response detector
        to accept a progress placeholder as the final answer.
        """
        page = await self.ensure_connected()
        any_check_ok = False  # True once at least one check returns a definitive answer

        # Check for streaming indicator
        try:
            if await page.locator(SEL_STREAMING).first.is_visible(timeout=500):
                return True
            any_check_ok = True
        except Exception:
            pass

        # Check stop button (most reliable during Pro extended thinking)
        for sel in (
            SEL_STOP_BUTTON,
            SEL_STOP_LEGACY_GENERATING,
            SEL_STOP_LEGACY_REASONING,
        ):
            try:
                if await page.locator(sel).first.is_visible(timeout=500):
                    return True
                any_check_ok = True
            except Exception:
                pass

        # Check for "Pro thinking" indicator (backup: a shimmer div that
        # appears during Pro extended thinking alongside placeholder text)
        msgs = await page.locator(SEL_ASSISTANT_MSG).all()
        if len(msgs) > self._msg_count_before_send:
            last = msgs[-1]
            try:
                shimmer = last.locator("div.loading-shimmer")
                if await shimmer.count():
                    text = (await shimmer.first.inner_text(timeout=500)).strip().lower()
                    if "thinking" in text:
                        return True
                any_check_ok = True
            except Exception:
                pass

            # Message exists but empty — still in pre-streaming phase
            text = await self.get_last_response()
            if not text:
                return True

        # If every check threw, assume still generating to avoid false
        # negatives from transient CDP glitches during long sessions.
        if not any_check_ok:
            logger.warning("All is_generating checks failed; assuming still generating")
            return True

        return False

    async def has_completion_indicators(self) -> bool:
        """Check whether completion action buttons are visible.

        Completion buttons (Copy, Good response, etc.) are NOT inside the
        assistant message div on project pages — they live in a sibling or
        ancestor container.  Search at page level using data-testid selectors.
        """
        page = await self.ensure_connected()

        for sel in SEL_COMPLETION_BUTTONS:
            try:
                if await page.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def new_chat(self) -> None:
        """Start a new chat conversation.

        If currently inside a project, navigates back to the project's
        overview URL so the new chat stays within the project.  Sending
        from the ``/project`` overview page is handled robustly by
        ``_verify_project_page_send``.
        """
        page = await self.ensure_connected()

        # Detect if we're in a project (URL contains /g/)
        project_url = None
        if "/g/" in page.url:
            # Extract the project base URL: /g/g-p-<id>-<name>/project
            parts = page.url.split("/g/")
            if len(parts) >= 2:
                slug = parts[1].split("/")[0]  # g-p-<id>-<name>
                project_url = f"https://chatgpt.com/g/{slug}/project"

        if project_url:
            await page.goto(
                project_url, wait_until="domcontentloaded", timeout=30_000
            )
        else:
            btn = page.locator(SEL_NEW_CHAT).first
            try:
                await btn.click(timeout=5_000)
            except Exception:
                await page.goto(
                    "https://chatgpt.com/", wait_until="domcontentloaded",
                    timeout=30_000,
                )

        await page.wait_for_selector(SEL_PROMPT_TEXTAREA, timeout=10_000)
        self._msg_count_before_send = 0
        logger.info("Opened new chat: %s", page.url)

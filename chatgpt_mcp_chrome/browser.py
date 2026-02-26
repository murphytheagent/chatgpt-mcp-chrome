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
SEL_MAIN_ARTICLES = "main article"
SEL_ASSISTANT_MSG = 'div[data-message-author-role="assistant"]'
SEL_STOP_BUTTON = 'button[aria-label="Stop generating"]'
SEL_STOP_REASONING = 'button[aria-label="Stop reasoning"]'
SEL_STREAMING = ".result-streaming"
SEL_COPY_BUTTON = 'article button[aria-label="Copy"]'
SEL_GOOD_RESPONSE = 'article button[aria-label="Good response"]'
SEL_READ_ALOUD = 'article button[aria-label="Read aloud"]'
SEL_NEW_CHAT = '[data-testid="create-new-chat-button"]'
SEL_MODEL_SWITCHER = '[data-testid="model-switcher-dropdown-button"]'
SEL_FILE_INPUT = 'input[type="file"]'
SEL_ATTACH_BUTTON = 'button[aria-label="Attach files"]'

CDP_URL = os.environ.get("CHATGPT_CDP_URL", "http://127.0.0.1:9222")
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

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._article_count_before_send: int = 0

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
        for pg in ctx.pages:
            if "chatgpt.com" in pg.url:
                self._page = pg
                logger.info("Reusing existing ChatGPT tab: %s", pg.url)
                return

        self._page = await ctx.new_page()
        await self._page.goto(
            "https://chatgpt.com/", wait_until="domcontentloaded", timeout=30_000
        )
        await self._page.wait_for_selector(SEL_PROMPT_TEXTAREA, timeout=15_000)
        logger.info("Opened new ChatGPT tab")

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
        """Disconnect from Chrome (does **not** close the browser)."""
        if self._playwright:
            await self._playwright.stop()
        self._playwright = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------
    # Model selection
    # ------------------------------------------------------------------

    async def select_model(self, model: str | None) -> ModelConfig:
        """Select a model via the dropdown menu.

        Opens the model-switcher dropdown, clicks the target menu item by
        its ``data-testid``, then waits for the menu to close.
        """
        page = await self.ensure_connected()
        config = get_model_config(model)

        # Check if the target model is already selected
        switcher = page.locator(SEL_MODEL_SWITCHER).first
        try:
            label = await switcher.get_attribute("aria-label") or ""
            # e.g. "Model selector, current model is 5.2 Pro"
            if config.display_name.lower() in label.lower():
                logger.info("Model '%s' already selected", config.display_name)
                return config
        except Exception:
            pass

        # Open the dropdown
        await switcher.click(timeout=5_000)
        await asyncio.sleep(0.5)

        # Click the target menu item
        item = page.locator(f'[data-testid="{config.dropdown_testid}"]').first
        await item.click(timeout=5_000)
        await asyncio.sleep(1)

        logger.info("Selected model '%s'", config.display_name)
        return config

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
        self._article_count_before_send = 0
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

        # Record article count BEFORE sending so we can detect a new response
        articles = await page.locator(SEL_MAIN_ARTICLES).all()
        self._article_count_before_send = len(articles)

        textarea = page.locator(SEL_PROMPT_TEXTAREA)
        await textarea.wait_for(state="visible", timeout=10_000)
        await textarea.click()
        await textarea.fill(prompt)
        await asyncio.sleep(0.3)

        send_btn = page.locator(SEL_SEND_BUTTON).first
        try:
            await send_btn.click(timeout=3_000)
        except Exception:
            await textarea.press("Enter")
        logger.info("Message sent (%d chars)", len(prompt))

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

        articles = await page.locator(SEL_MAIN_ARTICLES).all()
        if not articles:
            return ""

        # Only consider articles that appeared AFTER we sent the message
        if len(articles) <= self._article_count_before_send:
            return ""

        last_article = articles[-1]

        # The .markdown div is the authoritative signal: it only appears once
        # the actual response text starts rendering.  During Pro/Thinking
        # "thinking" phases the article exists but contains only a thinking
        # indicator — no .markdown div.
        md = last_article.locator(".markdown").first
        try:
            if await md.count():
                text = await self._extract_text_with_latex(md)
                if text and text.lower() not in TRANSIENT_TEXTS:
                    return text
        except Exception:
            pass

        # Fallback: .prose div (some response formats)
        prose = last_article.locator(".prose").first
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

        articles = await page.locator(SEL_MAIN_ARTICLES).all()
        if not articles:
            return []

        last = articles[-1]

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
        """Return *True* while ChatGPT is actively generating or thinking."""
        page = await self.ensure_connected()

        # Check for streaming indicator (most reliable)
        streaming = page.locator(SEL_STREAMING).first
        try:
            if await streaming.is_visible(timeout=500):
                return True
        except Exception:
            pass

        # Check stop buttons
        for sel in (SEL_STOP_BUTTON, SEL_STOP_REASONING):
            btn = page.locator(sel).first
            try:
                if await btn.is_visible(timeout=500):
                    return True
            except Exception:
                pass

        # Check if a new article has appeared but has no usable text yet
        # (model is still "thinking" before streaming starts)
        articles = await page.locator(SEL_MAIN_ARTICLES).all()
        if len(articles) > self._article_count_before_send:
            text = await self.get_last_response()
            if not text:
                # Article exists but empty — still generating
                return True

        return False

    async def has_completion_indicators(self) -> bool:
        """Check whether Copy / Good-response buttons are visible on the last article."""
        page = await self.ensure_connected()

        articles = await page.locator(SEL_MAIN_ARTICLES).all()
        if not articles:
            return False

        last = articles[-1]
        for sel in (
            'button[aria-label="Copy"]',
            'button[aria-label="Good response"]',
            'button[aria-label="Read aloud"]',
        ):
            try:
                if await last.locator(sel).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    async def new_chat(self) -> None:
        """Start a new chat conversation.

        If currently inside a project, navigates back to the project's
        home URL so the new chat stays within the project.
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
        self._article_count_before_send = 0
        logger.info("Opened new chat")

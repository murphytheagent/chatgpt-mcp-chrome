"""End-to-end test suite for ChatGPT MCP Chrome.

One comprehensive prompt per model type, minimal chat creation.

Usage:
    # Auto model only (1 chat, fast)
    python tests/e2e_suite.py

    # All models: Auto + Thinking + Pro (3 chats)
    python tests/e2e_suite.py --all-models

    # Specific model only
    python tests/e2e_suite.py --model thinking
    python tests/e2e_suite.py --model pro
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from chatgpt_mcp_chrome.browser import BrowserController
from chatgpt_mcp_chrome.response import ResponseDetector

# Set to a ChatGPT project name to route chats there, or None to skip.
PROJECT: str | None = "Murphy"


# ── Prompts ─────────────────────────────────────────────────────────
# Each model gets one comprehensive prompt that covers multiple objectives.

PROMPT_AUTO = (
    "Answer ALL of the following in a SINGLE response:\n\n"
    "A) PRIMES: List all prime numbers less than 30 and compute their sum.\n\n"
    "B) EQUATIONS: Write the quadratic formula and the Gaussian integral "
    "∫_{-∞}^{∞} e^{-x²} dx = √π using proper math notation.\n\n"
    "C) TABLE: Compare quicksort, mergesort, and heapsort — best, average, "
    "and worst case time complexity. Use a markdown table.\n\n"
    "D) ODE: Solve y'' + 4y = 0. Give the general solution.\n\n"
    "E) COMBINATORICS: How many distinct arrangements of the letters in "
    "MISSISSIPPI? Show the multinomial coefficient."
)

PROMPT_THINKING = (
    "Answer ALL of the following in a SINGLE response, showing full work:\n\n"
    "A) INTEGRAL: Compute ∫₀^∞ x² e^{-x} dx using integration by parts. "
    "Show every step.\n\n"
    "B) PROOF: Prove that √2 is irrational via proof by contradiction.\n\n"
    "C) SERIES: Derive the Taylor series of ln(1+x) around x=0 and state "
    "the radius of convergence."
)

PROMPT_PRO = (
    "Answer ALL of the following in a SINGLE response, with detailed work:\n\n"
    "A) BASEL: What is the sum of 1/1² + 1/2² + 1/3² + …? "
    "State the result and sketch a proof.\n\n"
    "B) COUNTING: How many ways to place 8 non-attacking rooks on an 8×8 "
    "chessboard? Explain the reasoning.\n\n"
    "C) PDE: Solve the heat equation u_t = k·u_xx on [0, L] with "
    "u(0,t) = u(L,t) = 0, u(x,0) = sin(πx/L). Give the solution."
)


# ── Checks per model ───────────────────────────────────────────────

def checks_auto(r: str) -> list[tuple[str, bool]]:
    return [
        ("primes sum = 129", "129" in r),
        ("LaTeX: $ delimiters", "$" in r),
        ("LaTeX: \\frac or \\sqrt", "frac" in r or "sqrt" in r),
        ("table: contains 'log'", "log" in r.lower()),
        ("ODE: sin/cos in solution", "sin" in r.lower() or "cos" in r.lower()),
        ("MISSISSIPPI = 34650", "34650" in r),
    ]

def checks_thinking(r: str) -> list[tuple[str, bool]]:
    return [
        ("LaTeX present", "$" in r),
        ("integral result = 2", "= 2" in r or "=2" in r or "$2$" in r),
        ("proof: contradiction/irrational",
         "contradiction" in r.lower() or "irrational" in r.lower()),
        ("Taylor: ln(1+x)", "ln" in r.lower() or "\\ln" in r),
        ("radius of convergence",
         "convergence" in r.lower() or "radius" in r.lower()),
    ]

def checks_pro(r: str) -> list[tuple[str, bool]]:
    return [
        ("LaTeX present", "$" in r),
        ("Basel: π²/6",
         "6" in r and ("pi" in r.lower() or "\\pi" in r or "π" in r)),
        ("rooks: 8! = 40320", "40320" in r),
        ("heat eq: sin or exponential",
         "sin" in r.lower() or "e^{" in r or "exp" in r.lower()),
    ]


# ── Multi-turn follow-up (reuses the Auto chat) ────────────────────

FOLLOWUP_PROMPT = (
    "From part A above, what is the largest prime you listed? "
    "Multiply it by 3. Just give the number."
)


# ── File download (separate chat, Auto model) ──────────────────────

FILE_PROMPT = (
    "Create two downloadable files:\n"
    "1. A CSV named 'students.csv' with columns Name, Score, Grade "
    "and 3 rows.\n"
    "2. A JSON named 'config.json' with keys: name, version, debug."
)


# ── Runner ──────────────────────────────────────────────────────────

class TestRunner:
    def __init__(self):
        self.browser = BrowserController()
        self.detector = ResponseDetector(self.browser)
        self.results: list[dict] = []

    def _record(self, name: str, passed: bool, detail: str = ""):
        status = "PASS" if passed else "FAIL"
        self.results.append({"name": name, "status": status, "detail": detail})
        mark = "+" if passed else "X"
        suffix = f"  ({detail})" if detail and not passed else ""
        print(f"  [{mark}] {name}{suffix}")

    async def _start_chat(self, model: str):
        if PROJECT:
            await self.browser.navigate_to_project(PROJECT)
        await self.browser.new_chat()
        await asyncio.sleep(1)
        self._config = await self.browser.select_model(model)

    async def _send(self, prompt: str) -> tuple[bool, str, float]:
        t0 = time.monotonic()
        await self.browser.send_message(prompt)
        completed, response = await self.detector.wait_for_response(self._config)
        return completed, response, time.monotonic() - t0

    async def run_model_test(self, model: str, prompt: str,
                              check_fn, label: str):
        print(f"\n{'='*60}")
        print(f"CHAT: {label} ({model})")
        print(f"{'='*60}")
        await self._start_chat(model)

        completed, r, elapsed = await self._send(prompt)
        print(f"Time: {elapsed:.1f}s | Len: {len(r)} | Completed: {completed}")
        print(f"Response:\n{r[:1500]}{'...' if len(r) > 1500 else ''}\n")

        self._record(f"{label}: completed", completed)
        self._record(f"{label}: length > 100", len(r) > 100, f"got {len(r)}")
        for name, passed in check_fn(r):
            self._record(f"{label}: {name}", passed)

        return completed, r

    async def run_multi_turn(self):
        """Send follow-up in the existing Auto chat (no new chat)."""
        print(f"\n--- Multi-turn follow-up (same chat) ---")
        completed, r, elapsed = await self._send(FOLLOWUP_PROMPT)
        print(f"Time: {elapsed:.1f}s | Response: {r[:100]}")
        self._record("multi-turn: completed", completed)
        self._record("multi-turn: 29×3 = 87", "87" in r)

    async def run_file_test(self):
        """File download test (needs its own chat)."""
        print(f"\n{'='*60}")
        print("CHAT: File download (auto)")
        print(f"{'='*60}")
        await self._start_chat("auto")
        completed, r, elapsed = await self._send(FILE_PROMPT)
        print(f"Time: {elapsed:.1f}s | Response: {r[:300]}")
        self._record("file: completed", completed)

        downloaded = await self.browser.download_generated_files()
        print(f"Downloaded: {downloaded}")
        self._record("file: ≥1 downloaded", len(downloaded) >= 1,
                      f"got {len(downloaded)}")
        for f in downloaded:
            try:
                with open(f) as fh:
                    content = fh.read()
                print(f"  {os.path.basename(f)}: {content[:200]}")
                self._record(f"file: {os.path.basename(f)} not empty",
                             len(content) > 5)
            except Exception as e:
                self._record(f"file: read {os.path.basename(f)}", False, str(e))

    def print_summary(self):
        print(f"\n{'='*60}")
        print("SUMMARY")
        print(f"{'='*60}")
        for c in self.results:
            mark = "+" if c["status"] == "PASS" else "X"
            line = f"  [{mark}] {c['name']}"
            if c["status"] != "PASS" and c["detail"]:
                line += f"  ({c['detail']})"
            print(line)
        passed = sum(1 for c in self.results if c["status"] == "PASS")
        total = len(self.results)
        print(f"\n{passed}/{total} checks passed")

    async def close(self):
        await self.browser.close()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--all-models", action="store_true",
                        help="Run Auto + Thinking + Pro (3 model chats)")
    parser.add_argument("--model", type=str, default=None,
                        help="Run a specific model only (auto, thinking, pro)")
    parser.add_argument("--skip-files", action="store_true",
                        help="Skip file download test")
    args = parser.parse_args()

    runner = TestRunner()
    try:
        models_to_run = []
        if args.model:
            models_to_run = [args.model]
        elif args.all_models:
            models_to_run = ["auto", "thinking", "pro"]
        else:
            models_to_run = ["auto"]

        for model in models_to_run:
            if model == "auto":
                await runner.run_model_test(
                    "auto", PROMPT_AUTO, checks_auto, "Auto")
                await runner.run_multi_turn()
            elif model == "thinking":
                await runner.run_model_test(
                    "thinking", PROMPT_THINKING, checks_thinking, "Thinking")
            elif model == "pro":
                await runner.run_model_test(
                    "pro", PROMPT_PRO, checks_pro, "Pro")

        if not args.skip_files and "auto" in models_to_run:
            await runner.run_file_test()

        runner.print_summary()
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())

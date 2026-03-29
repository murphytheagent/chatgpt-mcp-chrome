# Changelog

## 2026-03-28

### Fixed
- **Short response false completions**: Added `SHORT_RESPONSE_THRESHOLD` (500 chars) with re-check logic. Short responses that show completion indicators now wait 2 extra poll intervals and verify the text hasn't changed and the model hasn't resumed generating. Prevents premature return of placeholder text like "I'm checking...".
- **Stability checks increased**: Bumped `STABILITY_CHECKS` from 3 to 5 consecutive identical snapshots before declaring response complete. Reduces false positives on ChatGPT Pro's longer thinking phases.

## 2026-03-27

### Fixed
- **Model selector brittleness**: Replaced `data-testid` based model selection with text-based matching. Graceful degradation when model switcher UI changes.

### Changed
- **ChatGPT UI selector migration**: Updated selectors for ChatGPT 5.4+ UI changes, added diagnostic logging and validation scripts (AGENT-027).

## 2026-03-26

### Added
- **Consult conversation history persistence**: Conversation context now persists across MCP calls.
- **Auto-new-chat**: Fresh chat opened on first `ask()` per MCP session.

### Fixed
- **Response extraction**: Use last `.markdown` div instead of first, fixing extraction of multi-turn responses.
- **is_generating() false negatives**: Handle transient CDP glitches that caused premature completion detection.
- **Stop button detection**: Updated for ChatGPT 5.4 UI.
- **Premature completion on short progress-placeholder responses**: Added wait-and-recheck logic for brief intermediate texts.

### Changed
- **Removed project parameter** from `consult.ask()` tool for cleaner API.

## 2026-03-25

### Added
- **Per-slot persistent tab isolation**: Multi-agent parallel dispatch with isolated browser tabs per slot.
- **Pro thinking effort control**: Standard vs Extended thinking modes.

### Changed
- **Default mode set to "deep"**: Prefer extended reasoning by default.
- **Renamed parameters**: `model` to `mode`, `thinking` to `standard`, `pro` to `deep`.
- **Increased timeouts**: Standard 1h, deep 2h.

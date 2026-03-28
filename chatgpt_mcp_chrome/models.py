"""Model configuration: dropdown test IDs and per-model timeouts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    # Text fragment to match inside the dropdown menu item (case-insensitive).
    # The selector logic in browser.py searches all menu items for this text.
    dropdown_text: str
    timeout_sec: int
    display_name: str
    # Pro thinking effort: None (not applicable), "Standard", or "Extended"
    thinking_effort: str | None = None


# These map to the actual ChatGPT dropdown options.
# dropdown_text is matched (case-insensitive substring) against menu item text.
MODEL_CONFIGS: dict[str, ModelConfig] = {
    "standard": ModelConfig("pro", 3600, "Standard", "Standard"),
    "deep": ModelConfig("pro", 7200, "Deep", "Extended"),
}

# Aliases — keep old names working for backward compat
ALIASES: dict[str, str] = {
    "thinking": "standard",
    "auto": "standard",
    "instant": "standard",
    "fast": "standard",
    "quick": "standard",
    "pro": "deep",
    "research": "deep",
}

DEFAULT_MODEL = "deep"


def get_model_config(model: str | None) -> ModelConfig:
    """Resolve a model name to its config."""
    if model is None:
        return MODEL_CONFIGS[DEFAULT_MODEL]
    key = model.lower().strip()
    key = ALIASES.get(key, key)
    if key in MODEL_CONFIGS:
        return MODEL_CONFIGS[key]
    # Unknown — default
    return MODEL_CONFIGS[DEFAULT_MODEL]

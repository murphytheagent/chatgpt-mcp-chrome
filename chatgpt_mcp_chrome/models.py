"""Model configuration: dropdown test IDs and per-model timeouts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    # data-testid of the dropdown menu item, e.g. "model-switcher-gpt-5-2-pro"
    dropdown_testid: str
    timeout_sec: int
    display_name: str


# These map to the actual ChatGPT 5.2 dropdown options.
# data-testid values come from the model switcher menu.
MODEL_CONFIGS: dict[str, ModelConfig] = {
    "auto": ModelConfig("model-switcher-gpt-5-2", 300, "Auto"),
    "instant": ModelConfig("model-switcher-gpt-5-2-instant", 120, "Instant"),
    "thinking": ModelConfig("model-switcher-gpt-5-2-thinking", 900, "Thinking"),
    "pro": ModelConfig("model-switcher-gpt-5-2-pro", 1800, "Pro"),
}

# Aliases for convenience
ALIASES: dict[str, str] = {
    "gpt-5": "auto",
    "gpt-5.2": "auto",
    "5.2": "auto",
    "fast": "instant",
    "quick": "instant",
    "deep": "pro",
    "research": "pro",
}

DEFAULT_MODEL = "thinking"


def get_model_config(model: str | None) -> ModelConfig:
    """Resolve a model name to its config."""
    if model is None:
        return MODEL_CONFIGS[DEFAULT_MODEL]
    key = model.lower().strip()
    key = ALIASES.get(key, key)
    if key in MODEL_CONFIGS:
        return MODEL_CONFIGS[key]
    # Unknown — default to auto
    return MODEL_CONFIGS[DEFAULT_MODEL]

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
    "standard": ModelConfig("model-switcher-gpt-5-2-thinking", 900, "Standard"),
    "deep": ModelConfig("model-switcher-gpt-5-2-pro", 1800, "Deep"),
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

DEFAULT_MODEL = "standard"


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

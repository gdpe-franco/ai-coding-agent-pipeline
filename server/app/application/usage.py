from __future__ import annotations

from typing import Any


def merge_usage(
    current: dict[str, Any] | None, addition: dict[str, Any] | None
) -> dict[str, Any] | None:
    if addition is None:
        return current
    if current is None:
        return addition

    current_tokens = current.get("usage") or {}
    addition_tokens = addition.get("usage") or {}
    has_tokens = bool(current_tokens or addition_tokens)
    return {
        "model_responses": current.get("model_responses", 0) + addition.get("model_responses", 0),
        "duration_ms": current.get("duration_ms", 0) + addition.get("duration_ms", 0),
        "usage": (
            {
                key: current_tokens.get(key, 0) + addition_tokens.get(key, 0)
                for key in ("input_tokens", "output_tokens", "total_tokens")
            }
            if has_tokens
            else None
        ),
        "usage_complete": current.get("usage_complete", True)
        and addition.get("usage_complete", True),
    }

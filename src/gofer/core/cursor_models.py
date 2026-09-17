"""Cursor encodes effort and execution variants in its model IDs."""

from __future__ import annotations

import re

# Match whole trailing tokens, never fragments such as "low" in "lowlatency".
# Keep suffix spelling intact: xhigh and extra-high are distinct CLI IDs.
_VARIANT = re.compile(
    r"^(.+?)-((?:extra-high|xhigh|minimal|none|low|medium|high|max|ultra)"
    r"(?:-(?:fast|thinking))*|fast)$"
)


def split_cursor_model(model: str) -> tuple[str, str | None]:
    match = _VARIANT.fullmatch(model)
    return (match[1], match[2]) if match else (model, None)


def cursor_model_id(model: str | None, effort: str | None) -> str | None:
    """Turn a catalog selection back into a native --model argument."""
    if not effort or effort == "cli-default":
        return model
    if not model or model == "cli-default":
        raise ValueError("Cursor effort requires an explicit model")
    if split_cursor_model(f"model-{effort}") != ("model", effort):
        raise ValueError(f"Unsupported Cursor effort '{effort}'")
    base, _ = split_cursor_model(model)
    return f"{base}-{effort}"

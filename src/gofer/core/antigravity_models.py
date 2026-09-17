"""Antigravity advertises effort variants as native model IDs."""

import re


def split_antigravity_model(model: str) -> tuple[str, str | None]:
    match = re.fullmatch(r"(.+)-(low|medium|high)", model)
    return (match[1], match[2]) if match else (model, None)

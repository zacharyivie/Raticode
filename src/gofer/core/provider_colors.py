"""Each usage provider's chart color, taken from its public branding.

This is the one copy of the palette. The usage history series carries each provider's
style, so the desktop chart, the phone chart and anything else that draws it read the
same values from here.

Every style has a color for dark themes, a color for light themes and a pattern of its
own, so color is never the only cue (orange and green look alike to many people). Both
colors keep at least 3:1 contrast with the app's surfaces in that theme, and providers
that share a parent brand (Claude Code and the Anthropic API, Codex and the OpenAI API)
differ by shade and pattern. Near-black and near-white brand marks swap to their light or
dark logo version so they stay visible. ``tests/unit/test_provider_colors.py`` checks
contrast and how far apart each pair is.

Sources, read 2026-11-17:

- Claude Code, Anthropic API: Anthropic's brand guidelines skill, "Orange: #d97757 -
  Primary accent" (https://github.com/anthropics/skills, skills/brand-guidelines). The
  Anthropic API uses a lighter tint of that clay on dark themes and a deeper one on light.
- Codex, OpenAI API (and local model servers, which record as the OpenAI API): OpenAI's
  green, #10A37F, as used in ChatGPT. openai.com/brand refused automated reads, so this
  comes from published palettes. The OpenAI API uses a mint tint and a deep green.
- GitHub Copilot: purple from GitHub's brand toolkit palette
  (https://brand.github.com/foundations/color), "Purple 4" #8534F3, with "Purple 3" #B870FF
  on dark themes. That page lists the purples but doesn't itself name them for Copilot.
- Cursor: its monochrome mark. https://cursor.com/brand sets theme colors #f7f7f4 (light)
  and #14120b (dark); the chart uses the cream on dark themes and #26251E ink on light.
- OpenCode: https://opencode.ai/brand gives the logo inks, #EEEEEE/#AEAEAE on dark and
  #1F1F1F/#626262 on light; the chart uses the 70% inks.
- Grok: xAI's black and white mark (https://x.ai/legal/brand-guidelines). Cursor and
  OpenCode already use the lightest grays, so Grok is a mid gray on dark themes and a dark
  gray on light ones, with a cross pattern for its X.
- Antigravity: the logo's fill on https://antigravity.google/, #3186FF.
- Jev (TypeSafe): the accent on https://typesafe.ai/, #D45BB6, lifted on dark themes.

Split by account, the chart draws each account of a provider in a shade of that provider's
color with a pattern of its own (``account_style``). The default sign-in keeps the brand
color and pattern, so a single account looks the same in both views. The other shades are
the brand color mixed toward white or black, picked to stay as far apart as they can while
keeping 3:1 contrast with the chart's surfaces.
"""

from __future__ import annotations

import math
import re
from functools import cache
from typing import Final, Literal, TypedDict


class ProviderStyle(TypedDict):
    dark: str
    light: str
    pattern: str


PATTERNS: Final = frozenset(
    {
        "solid",
        "stripes",
        "back-stripes",
        "horizontal",
        "vertical",
        "dots",
        "grid",
        "cross",
        "checks",
        "rings",
    }
)
HEX_COLOR: Final = re.compile(r"#[0-9A-Fa-f]{6}")

PROVIDER_STYLES: Final[dict[str, ProviderStyle]] = {
    "claude_code": {"dark": "#D97757", "light": "#C6613F", "pattern": "solid"},
    "anthropic_api": {"dark": "#EDB59B", "light": "#8F4A2E", "pattern": "back-stripes"},
    "codex": {"dark": "#10A37F", "light": "#0D8A6B", "pattern": "stripes"},
    "openai_api": {"dark": "#7FD8BE", "light": "#075A45", "pattern": "checks"},
    "copilot": {"dark": "#B870FF", "light": "#8534F3", "pattern": "horizontal"},
    "cursor": {"dark": "#F7F7F4", "light": "#26251E", "pattern": "dots"},
    "opencode": {"dark": "#AEAEAE", "light": "#626262", "pattern": "vertical"},
    "grok": {"dark": "#787878", "light": "#444444", "pattern": "cross"},
    "antigravity": {"dark": "#3186FF", "light": "#1A6FE8", "pattern": "grid"},
    "jev": {"dark": "#E36BC4", "light": "#B03C93", "pattern": "rings"},
}

# Other names the ledger and older records use for the same providers.
ALIASES: Final = {
    "claude": "claude_code",
    "anthropic": "anthropic_api",
    "openai": "openai_api",
    "agy": "antigravity",
}

# A provider with no brand entry, such as "unknown": olive, away from every brand hue.
FALLBACK_STYLE: Final[ProviderStyle] = {"dark": "#A8A24A", "light": "#7A7424", "pattern": "stripes"}


def provider_style(provider: str | None) -> ProviderStyle:
    """The chart style for a usage provider, or the fallback for one without a brand."""
    name = str(provider or "")
    style = PROVIDER_STYLES.get(ALIASES.get(name, name), FALLBACK_STYLE)
    return {"dark": style["dark"], "light": style["light"], "pattern": style["pattern"]}


Theme = Literal["dark", "light"]

# The surfaces the chart sits on: desktop (frontend/src/styles/index.css --color-surface and
# --color-canvas) and phone (mobile/android/.../ui/Theme.kt surface, background, surfaceVariant).
CHART_SURFACES: Final[dict[Theme, tuple[str, ...]]] = {
    "dark": ("#18181A", "#0B0B0C", "#242426", "#1E1E1E", "#27272A"),
    "light": ("#F4F0E7", "#E4DFD5", "#FFFFFF", "#F5F6FA", "#EAECF3"),
}
# Shades per provider before they repeat. Patterns repeat only after all ten.
ACCOUNT_SHADES: Final = 4
# Patterns for a provider's second, third, ... account, after its brand pattern.
ACCOUNT_PATTERNS: Final = (
    "dots",
    "stripes",
    "grid",
    "back-stripes",
    "horizontal",
    "rings",
    "vertical",
    "checks",
    "cross",
    "solid",
)
# How far a shade may move toward white or black: past this the brand hue fades.
_MIX_STEPS: Final = tuple(step / 20 for step in range(1, 13))


def _channels(color: str) -> tuple[float, float, float]:
    def linear(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linear(int(color[i : i + 2], 16)) for i in (1, 3, 5))
    return r, g, b


def luminance(color: str) -> float:
    """WCAG relative luminance of a #RRGGBB color."""
    r, g, b = _channels(color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(first: str, second: str) -> float:
    """WCAG contrast ratio between two #RRGGBB colors, 1 to 21."""
    low, high = sorted((luminance(first), luminance(second)))
    return (high + 0.05) / (low + 0.05)


def lab(color: str) -> tuple[float, float, float]:
    """CIELAB (D65) coordinates of a #RRGGBB color."""
    r, g, b = _channels(color)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116

    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e_lab(first: tuple[float, float, float], second: tuple[float, float, float]) -> float:
    """CIEDE2000 difference: about 2 is just noticeable, 10 or more is clearly apart."""
    l1, a1, b1 = first
    l2, a2, b2 = second
    c_mean = (math.hypot(a1, b1) + math.hypot(a2, b2)) / 2
    g = 0.5 * (1 - math.sqrt(c_mean**7 / (c_mean**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = math.hypot(a1p, b1), math.hypot(a2p, b2)
    h1 = math.degrees(math.atan2(b1, a1p)) % 360
    h2 = math.degrees(math.atan2(b2, a2p)) % 360
    dl, dc = l2 - l1, c2p - c1p
    if c1p * c2p == 0:
        dh, h_mean = 0.0, h1 + h2
    else:
        diff = h2 - h1
        dh = diff if abs(diff) <= 180 else diff - 360 if diff > 0 else diff + 360
        if abs(h1 - h2) <= 180:
            h_mean = (h1 + h2) / 2
        else:
            h_mean = (h1 + h2 + 360) / 2 if h1 + h2 < 360 else (h1 + h2 - 360) / 2
    big_h = 2 * math.sqrt(c1p * c2p) * math.sin(math.radians(dh / 2))
    l_mean, cp_mean = (l1 + l2) / 2, (c1p + c2p) / 2
    t = (
        1
        - 0.17 * math.cos(math.radians(h_mean - 30))
        + 0.24 * math.cos(math.radians(2 * h_mean))
        + 0.32 * math.cos(math.radians(3 * h_mean + 6))
        - 0.20 * math.cos(math.radians(4 * h_mean - 63))
    )
    sl = 1 + 0.015 * (l_mean - 50) ** 2 / math.sqrt(20 + (l_mean - 50) ** 2)
    sc, sh = 1 + 0.045 * cp_mean, 1 + 0.015 * cp_mean * t
    rt = (
        -2
        * math.sqrt(cp_mean**7 / (cp_mean**7 + 25**7))
        * math.sin(math.radians(60 * math.exp(-(((h_mean - 275) / 25) ** 2))))
    )
    return math.sqrt(
        (dl / sl) ** 2 + (dc / sc) ** 2 + (big_h / sh) ** 2 + rt * (dc / sc) * (big_h / sh)
    )


def delta_e(first: str, second: str) -> float:
    """CIEDE2000 difference between two #RRGGBB colors."""
    return delta_e_lab(lab(first), lab(second))


def _mix(color: str, amount: float, toward: int) -> str:
    channels = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(v + (toward - v) * amount):02X}" for v in channels)


@cache
def _shades(color: str, theme: Theme) -> tuple[str, ...]:
    """The brand color, then up to ACCOUNT_SHADES - 1 tints and shades of it.

    Each next shade is the readable candidate farthest from every shade already chosen,
    so a provider's first few accounts are as easy to tell apart as its color allows.
    """
    surfaces = CHART_SURFACES[theme]
    candidates = [
        mixed
        for amount in _MIX_STEPS
        for mixed in (_mix(color, amount, 255), _mix(color, amount, 0))
        if all(contrast_ratio(mixed, surface) >= 3 for surface in surfaces)
    ]
    chosen = [color]
    while len(chosen) < ACCOUNT_SHADES and candidates:
        # max() keeps the first of equal candidates, so the result never varies.
        best = max(candidates, key=lambda c: min(delta_e(c, shade) for shade in chosen))
        chosen.append(best)
        candidates.remove(best)
    return tuple(chosen)


def account_style(provider: str | None, index: int) -> ProviderStyle:
    """The chart style of a provider's account: ``index`` 0 is its default sign-in.

    Index 0 is the brand style itself. Later accounts get a shade of the brand color and
    a pattern no earlier account of that provider uses (until all ten are taken).
    """
    brand = provider_style(provider)
    index = max(0, int(index))
    if index == 0:
        return brand
    patterns = [brand["pattern"], *(p for p in ACCOUNT_PATTERNS if p != brand["pattern"])]
    dark, light = _shades(brand["dark"], "dark"), _shades(brand["light"], "light")
    return {
        "dark": dark[index % len(dark)],
        "light": light[index % len(light)],
        "pattern": patterns[index % len(patterns)],
    }


def clean_style(value: object) -> ProviderStyle | None:
    """A style received from elsewhere, kept only when every field is well formed."""
    if not isinstance(value, dict):
        return None
    dark, light, pattern = value.get("dark"), value.get("light"), value.get("pattern")
    if not (isinstance(dark, str) and HEX_COLOR.fullmatch(dark)):
        return None
    if not (isinstance(light, str) and HEX_COLOR.fullmatch(light)):
        return None
    if pattern not in PATTERNS:
        return None
    return {"dark": dark, "light": light, "pattern": str(pattern)}

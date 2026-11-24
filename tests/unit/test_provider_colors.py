"""The usage chart's brand palette stays readable, complete and in one place."""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import get_args

import pytest

from gofer.classifiers.jev import USAGE_PROVIDER as JEV_PROVIDER
from gofer.core.provider_colors import (
    ACCOUNT_SHADES,
    ALIASES,
    CHART_SURFACES,
    FALLBACK_STYLE,
    PATTERNS,
    PROVIDER_STYLES,
    account_style,
    clean_style,
    contrast_ratio,
    delta_e,
    delta_e_lab,
    provider_style,
)
from gofer.core.provider_profiles import ProfileSubscription

ROOT = Path(__file__).resolve().parents[2]

# The surfaces the chart sits on: desktop (frontend/src/styles/index.css --color-surface and
# --color-canvas) and phone (mobile/android/.../ui/Theme.kt surface, background, surfaceVariant).
SURFACES = {
    "dark": ["#18181A", "#0B0B0C", "#242426", "#1E1E1E", "#27272A"],
    "light": ["#F4F0E7", "#E4DFD5", "#FFFFFF", "#F5F6FA", "#EAECF3"],
}
# A provider's first accounts: as many as get a shade of their own.
ACCOUNTS = range(ACCOUNT_SHADES)


def _contrast(a: str, b: str) -> float:
    return contrast_ratio(a, b)


def _delta_e(first: str, second: str) -> float:
    return delta_e(first, second)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        # Sharma, Wu and Dalal's CIEDE2000 test data (pairs 1, 7, 17 and 25).
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ],
)
def test_color_difference_matches_published_values(
    first: tuple[float, float, float], second: tuple[float, float, float], expected: float
) -> None:
    assert delta_e_lab(first, second) == pytest.approx(expected, abs=1e-4)


def test_the_palette_checks_the_surfaces_this_test_names() -> None:
    assert {theme: list(colors) for theme, colors in CHART_SURFACES.items()} == SURFACES
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21)


def test_every_provider_the_ledger_records_has_a_brand_style() -> None:
    recorded = {*get_args(ProfileSubscription), JEV_PROVIDER}
    assert recorded <= set(PROVIDER_STYLES)
    for alias, name in ALIASES.items():
        assert provider_style(alias) == provider_style(name)
    assert provider_style("unknown") == FALLBACK_STYLE
    assert provider_style(None) == FALLBACK_STYLE


def test_brand_colors_follow_each_brand() -> None:
    # The values the sources in provider_colors.py give; a change here needs a new source.
    assert provider_style("claude_code")["dark"] == "#D97757"
    assert provider_style("codex")["dark"] == "#10A37F"
    assert provider_style("copilot")["light"] == "#8534F3"
    assert provider_style("cursor") == {"dark": "#F7F7F4", "light": "#26251E", "pattern": "dots"}
    assert provider_style("opencode")["dark"] == "#AEAEAE"
    assert provider_style("antigravity")["dark"] == "#3186FF"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_color_stands_out_from_the_chart_background(theme: str) -> None:
    for name, style in [*PROVIDER_STYLES.items(), ("fallback", FALLBACK_STYLE)]:
        for surface in SURFACES[theme]:
            ratio = _contrast(style[theme], surface)  # type: ignore[literal-required]
            assert ratio >= 3, f"{name} {style[theme]} on {surface}: {ratio:.2f}"  # type: ignore[literal-required]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_any_two_providers_are_told_apart_by_color(theme: str) -> None:
    styles = [*PROVIDER_STYLES.items(), ("fallback", FALLBACK_STYLE)]
    for (first, a), (second, b) in itertools.combinations(styles, 2):
        distance = _delta_e(a[theme], b[theme])  # type: ignore[literal-required]
        assert distance >= 10, f"{first} and {second} in {theme}: {distance:.1f}"


def test_each_brand_has_its_own_pattern_so_color_is_not_the_only_cue() -> None:
    patterns = [style["pattern"] for style in PROVIDER_STYLES.values()]
    assert len(set(patterns)) == len(patterns)
    assert set(patterns) <= PATTERNS
    # Shared parent brands differ by pattern as well as shade.
    for first, second in (("claude_code", "anthropic_api"), ("codex", "openai_api")):
        assert PROVIDER_STYLES[first]["pattern"] != PROVIDER_STYLES[second]["pattern"]


def test_received_styles_are_kept_only_when_well_formed() -> None:
    good: dict[str, str] = {"dark": "#10A37F", "light": "#0D8A6B", "pattern": "stripes"}
    assert clean_style(good) == good
    assert clean_style(dict(good, extra="x")) == good
    assert clean_style(dict(good, dark="red")) is None
    assert clean_style(dict(good, light="#12345")) is None
    assert clean_style(dict(good, pattern="url(x)")) is None
    assert clean_style("#10A37F") is None
    assert clean_style(None) is None


def test_desktop_and_phone_draw_every_pattern_and_keep_no_brand_colors_of_their_own() -> None:
    css = (ROOT / "frontend/src/components/UsageDashboard.css").read_text()
    chart_js = (ROOT / "frontend/src/lib/usageHistory.js").read_text()
    phone_chart = ROOT / "mobile/android/app/src/main/java/dev/raticode/mobile/ui/UsageChart.kt"
    kotlin = phone_chart.read_text()
    phone_model = (
        ROOT / "mobile/android/app/src/main/java/dev/raticode/mobile/live/UsageHistory.kt"
    ).read_text()
    for pattern in PATTERNS - {"solid"}:
        assert f"[data-pattern={pattern}]" in css, pattern
        assert f'"{pattern}" ->' in kotlin, pattern
    for pattern in PATTERNS:
        assert f'"{pattern}"' in chart_js, pattern
        assert f'"{pattern}"' in phone_model, pattern
    for style in PROVIDER_STYLES.values():
        for color in (style["dark"], style["light"]):
            for source in (css, chart_js, kotlin, phone_model):
                assert color.lower() not in source.lower()
                assert f"0xFF{color[1:]}".lower() not in source.lower()


ALL_PROVIDERS = [*PROVIDER_STYLES, "unknown"]


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_account_shade_stands_out_from_the_chart_background(theme: str) -> None:
    for provider in ALL_PROVIDERS:
        for index in range(12):
            color = account_style(provider, index)[theme]  # type: ignore[literal-required]
            for surface in SURFACES[theme]:
                ratio = _contrast(color, surface)
                assert ratio >= 3, f"{provider} account {index} {color} on {surface}: {ratio:.2f}"


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_a_providers_accounts_are_told_apart_by_shade_and_pattern(theme: str) -> None:
    for provider in ALL_PROVIDERS:
        styles = [account_style(provider, index) for index in ACCOUNTS]
        for (i, a), (j, b) in itertools.combinations(enumerate(styles), 2):
            # Shades of one brand sit closer than two brands do; patterns carry the rest.
            distance = _delta_e(a[theme], b[theme])  # type: ignore[literal-required]
            assert distance >= 5, f"{provider} accounts {i} and {j} in {theme}: {distance:.1f}"
        # Every account of a provider has its own pattern, up to ten accounts.
        patterns = [account_style(provider, index)["pattern"] for index in range(len(PATTERNS))]
        assert len(set(patterns)) == len(PATTERNS)
        assert set(patterns) == PATTERNS


def test_the_default_account_keeps_its_brand_and_shades_follow_the_brand() -> None:
    for provider in ALL_PROVIDERS:
        assert account_style(provider, 0) == provider_style(provider)
        for index in range(1, 12):
            style = account_style(provider, index)
            assert clean_style(style) == style
    # A shade keeps the brand's hue: Claude Code's accounts stay clay, Codex's green.
    for provider, other in (("claude_code", "codex"), ("codex", "claude_code")):
        for index in ACCOUNTS:
            own = _delta_e(account_style(provider, index)["dark"], provider_style(provider)["dark"])
            far = _delta_e(account_style(provider, index)["dark"], provider_style(other)["dark"])
            assert own < far
    # Past the shades, colors repeat while patterns stay new.
    assert account_style("codex", ACCOUNT_SHADES)["dark"] == account_style("codex", 0)["dark"]
    assert account_style("codex", ACCOUNT_SHADES)["pattern"] != account_style("codex", 0)["pattern"]

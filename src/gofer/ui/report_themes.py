"""Report design guidance, independent of knowledge storage."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from gofer.ui.report_outputs import report_format_rules
from gofer.utils.atomic_output import (
    atomic_binary_output,
    mkdir_without_links,
    open_binary_input,
)

# The fields of a report theme setting that its rules depend on.
THEME_FIELDS = ("enabled", "theme", "format", "instructions")
MAX_THEME_FILE_BYTES = 64 * 1024

REPORT_THEME_PROMPTS = {
    "auto": "System: design coordinated light and dark palettes using prefers-color-scheme. "
    "Adapt every surface and text color together so both appearances remain readable.",
    "light": "Light: use a luminous editorial palette, crisp dark text, "
    "and deliberate color accents. "
    "Choose colors and typography that suit this report's subject.",
    "dark": "Dark: use deep surfaces, luminous readable text, and selective saturated accents. "
    "Create hierarchy through composition and tonal depth without washing out charts or labels.",
    "sepia": "Sepia: use warm paper tones, rich ink, and an editorial or field-notebook mood. "
    "Choose complementary accents and expressive typography suited to the subject.",
    "vaporwave": (
        "Vaporwave: use midnight violet, hot pink, and electric cyan with pale readable "
        "text. Pair oversized italic display headings with calm sans-serif body text; use "
        "sunset bands and retro window framing sparingly around the report."
    ),
    "steam": (
        "Steam: use parchment, soot, aged brass, and oxidized teal in a Victorian "
        "engineering journal. Pair slab-serif headings with bookish body text; organize "
        "evidence as annotated plates and measured diagrams, with fine mechanical rules."
    ),
    "carbon": (
        "Carbon: use matte graphite, silver-white text, and a sharp signal-orange accent. "
        "Use condensed sans-serif headings, tabular numerals, and precise technical tables; "
        "build a disciplined industrial layout with minimal ornament."
    ),
    "botanical": (
        "Botanical: use ivory paper, forest-green ink, moss, and muted terracotta. Pair "
        "botanical-book serif headings with readable body type; arrange findings as field "
        "observations with specimen-style captions and generous margins."
    ),
    "blueprint": (
        "Blueprint: use deep Prussian blue, chalk-white text, and cyan annotations. Treat "
        "real diagrams as drafting plates with fine dimension lines; use clear sans-serif "
        "body text and monospace for measurements, keeping grids behind diagrams only."
    ),
    "arcade": (
        "Arcade: use near-black plum, acid yellow, and bright coral like a vintage arcade "
        "cabinet. Give short headings a blocky display treatment, with ordinary readable "
        "body type; turn actual milestones into level-like sections without inventing "
        "scores."
    ),
    "sakura": (
        "Sakura: use warm ivory, dark plum ink, cherry-blossom pink, and restrained "
        "vermilion. Pair elegant serif headings with airy body text; compose asymmetric "
        "sections and delicate divider details with plenty of breathing room."
    ),
    "deep-sea": (
        "Deep Sea: use abyssal navy, pearl-white text, bioluminescent teal, and small coral "
        "highlights. Let the report descend through clearly labeled sections, with flowing "
        "contours around real charts and spacious, quiet typography."
    ),
    "solarpunk": (
        "Solarpunk: use sunlit cream, leaf-green ink, marigold, and sky blue. Combine "
        "optimistic geometric headings with humanist body text; favor open compositions and "
        "clear connected diagrams inspired by community gardens and solar architecture."
    ),
    "noir": (
        "Noir: use warm black, newspaper-white text, and one crimson accent. Pair cinematic "
        "serif headlines with restrained body text; present findings as an investigative "
        "dossier with strong captions and dramatic but readable negative space."
    ),
    "candy-lab": (
        "Candy Lab: use marshmallow cream, dark berry ink, bubblegum pink, and mint. "
        "Combine rounded display headings with clean body text; use playful oversized "
        "section markers and crisp experimental diagrams while keeping dense evidence easy "
        "to scan."
    ),
    "cosmic": (
        "Cosmic: use ink-blue space, starlight-white text, ultraviolet, and amber. Pair "
        "expansive display headings with steady body text; arrange related findings like a "
        "labeled star atlas, reserving orbital paths for real relationships."
    ),
}


# Every theme, built in or custom, gets these. Reports are read in the desktop app, in
# narrow side panes and in the phone app, whose web view enlarges text by the phone's
# font size setting and shows white under a page background that is only a gradient.
SCREEN_RULES = (
    "Every report must read well on a 360px-wide phone with its text enlarged up to "
    "200% by the phone's font size setting, in a narrow desktop pane and on a wide "
    "desktop window. These screen rules win over the theme where they conflict. Put "
    '<meta name="viewport" content="width=device-width, initial-scale=1"> in the head. '
    "Declare the palette's color scheme on :root (color-scheme: dark for a dark design, "
    "light for a light one, light dark when it follows prefers-color-scheme) so a phone "
    "in dark mode doesn't recolor the page. Give html a solid background-color in the "
    "palette's base color and put any page gradient on body, not html: Android's web "
    "view doesn't paint a gradient page background and shows white instead. Text never sits "
    "on decoration it can collide with: keep hero art, banners and suns in their own "
    "block, and below 760px put the title and its kicker in normal flow after the art, "
    "with no absolute positioning, transforms or negative margins pulling text over it. "
    "Size headings with clamp() in rem, keep uppercase letter-spacing at .04em or less "
    "for headings and .1em or less for kickers and labels below 600px, and let long "
    "words wrap with overflow-wrap: anywhere. Body text is at least 1rem. Text keeps at "
    "least 4.5:1 contrast on every surface it can sit on, including the plain page "
    "background when a gradient or image doesn't load. Never set a width wider than the "
    "screen: tables, diagrams and code scroll inside their own container. Leave at least "
    "48px of space after the last content so it clears a phone's navigation bar."
)


def report_theme_rules(config: dict[str, Any] | None) -> str:
    if not config:
        return ""
    output = report_format_rules(config["format"]) if config.get("format") else ""
    if config.get("enabled") is False:
        # No design direction, but an HTML report still has to fit a phone.
        if config.get("format") in {"html", "slides"}:
            return output + " " + SCREEN_RULES
        return output
    direction = config.get("instructions") or REPORT_THEME_PROMPTS.get(
        config.get("theme", "auto"), REPORT_THEME_PROMPTS["auto"]
    )
    return (
        output
        + " For generated HTML reports, slides, and PDFs, use this design direction: "
        + str(direction)[:12000]
        + " Make the composition specific to the findings, with expressive typography, "
        "generous spacing, and clear hierarchy. Use diagrams, charts, tables, or annotated "
        "evidence where they explain the findings. Do not invent data for decoration. "
        "Write a complete standalone HTML document with embedded CSS and essential assets, "
        "responsive layout, accessible contrast, semantic structure, and readable print styles. "
        "Do not depend on remote fonts or scripts. Preserve each report's authored styling. "
        "This preference applies wherever a report is saved. It does not require Second Brain "
        "or change the user's requested output format. " + SCREEN_RULES
    )


def report_rules_pointer(tools: list[str]) -> str:
    """The one line a turn prompt carries instead of the report rules themselves."""
    named = " or ".join(f"`rules` on {tool}" for tool in tools)
    return (
        "Before you write a report, slides, a PDF or another informational file (notes, "
        f"summaries, audits, plans, handoffs, guides), call {named} for the report format "
        "and design rules."
    )


def write_theme_file(data_dir: Path, config: dict[str, Any]) -> Path:
    """The theme setting as a file a report or Second Brain tool server reads its rules from.

    A custom theme is up to 12,000 characters, too long for a server's command line, which
    some providers copy into their own. Files are named by their content, so a turn reuses
    the file an earlier turn wrote for the same setting. A file under that name with other
    contents is replaced, so the turn always reads Raticode's own setting.
    """
    payload = json.dumps(
        {key: config[key] for key in THEME_FIELDS if key in config}, sort_keys=True
    ).encode("utf-8")
    folder = data_dir.resolve() / "report-themes"
    mkdir_without_links(folder)
    target = folder / f"{_content_name(payload)}.json"
    try:
        with atomic_binary_output(target, exclusive=True) as output:
            output.write(payload)
        return target
    except FileExistsError:
        pass
    try:
        with open_binary_input(target) as handle:
            if handle.read(MAX_THEME_FILE_BYTES + 1) == payload:
                return target
    except OSError:
        pass
    with atomic_binary_output(target) as output:
        output.write(payload)
    return target


def _content_name(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()[:32]


def write_rules_file(folder: Path, config: dict[str, Any] | None) -> Path | None:
    """The report format and design rules as a text file a turn without report tools reads.

    A read-only review never saves a report, so it only needs the rules to judge one.
    The file replaces whatever has its name, so the turn always reads Raticode's text.
    Named by content, so a resumed session's instructions stay the same.
    """
    rules = report_theme_rules(config)
    if not rules:
        return None
    payload = rules.encode("utf-8")
    mkdir_without_links(folder)
    target = folder.resolve() / f"report-rules-{hashlib.sha256(payload).hexdigest()[:16]}.txt"
    with atomic_binary_output(target) as output:
        output.write(payload)
    return target


def report_rules_file_pointer(path: str) -> str:
    """The one line a turn with no report tools carries instead of the report rules."""
    return (
        "This turn has no report tools. The report format and design rules are in the file "
        f"{path}; read it when you need to judge a report's format or design."
    )


def read_theme_file(path: Path) -> dict[str, Any]:
    """The theme setting write_theme_file stored. Raises ValueError for anything else."""
    try:
        with open_binary_input(path) as handle:
            data = handle.read(MAX_THEME_FILE_BYTES + 1)
        config = json.loads(data) if len(data) <= MAX_THEME_FILE_BYTES else None
    except (OSError, ValueError) as exc:
        raise ValueError("The report theme file can't be read.") from exc
    if _content_name(data) != path.stem:
        raise ValueError("The report theme file changed after Raticode wrote it.")
    if not isinstance(config, dict) or set(config) - set(THEME_FIELDS):
        raise ValueError("The report theme file isn't a report theme setting.")
    if not all(
        isinstance(config.get(key, ""), str) for key in ("theme", "format", "instructions")
    ) or not isinstance(config.get("enabled", True), bool):
        raise ValueError("The report theme file isn't a report theme setting.")
    return config

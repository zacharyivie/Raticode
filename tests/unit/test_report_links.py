"""Links in saved reports and the Library health dead-link list and repair."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from gofer.ui.brain_library import BrainLibrary
from gofer.ui.report_links import (
    ABSOLUTE,
    MISSING,
    OUTSIDE,
    TEMPORARY,
    check_link,
    rewrite_links,
)
from gofer.ui.report_outputs import save_report
from tests.helpers.brain_library import wait_task

# A path that is neither inside a test brain nor in a temporary folder.
OUTSIDE_FILE = "/usr/share/raticode-test-missing/figure.png"


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "Reports/Topic").mkdir(parents=True)
    (root / "Assets").mkdir()
    (root / "Assets/chart one.png").write_bytes(b"png")
    (root / "Reports/Topic/other.html").write_text("<p>other</p>")
    return root


def test_check_link_classifies_by_resolved_location(vault: Path, tmp_path: Path) -> None:
    folder = vault / "Reports/Topic"
    chart = vault / "Assets/chart one.png"
    inside = check_link(str(chart), vault, folder)
    assert (inside.reason, inside.relative) == (ABSOLUTE, "../../Assets/chart%20one.png")
    as_url = check_link(chart.as_uri() + "#top", vault, folder)
    assert (as_url.reason, as_url.relative) == (ABSOLUTE, "../../Assets/chart%20one.png#top")
    # A dotted absolute path that walks back in is still inside.
    dotted = check_link(f"{vault}/Reports/../Assets/chart%20one.png", vault, folder)
    assert dotted.relative == "../../Assets/chart%20one.png"
    # A symlinked alias of the brain resolves to the brain.
    alias = tmp_path / "alias"
    alias.symlink_to(vault)
    assert check_link(f"{alias}/Reports/Topic/other.html", vault, folder).relative == "other.html"
    # A symlink inside the brain that points out is outside, not inside.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (vault / "escape").symlink_to(elsewhere)
    assert check_link(f"{vault}/escape/file.png", vault, folder).reason in {TEMPORARY, OUTSIDE}
    assert check_link("/tmp/raticode-test/x.png", vault, folder).reason == TEMPORARY
    assert check_link(OUTSIDE_FILE, vault, folder).reason == OUTSIDE
    assert check_link("../../../../outside.md", vault, folder).reason in {TEMPORARY, OUTSIDE}
    assert check_link("other.html", vault, folder).reason is None
    assert check_link("gone.html", vault, folder).reason == MISSING
    for fine in ("https://example.com/a", "#section", "mailto:a@b.c", "//cdn.example/x.js", ""):
        assert check_link(fine, vault, folder).reason is None


def test_saving_an_html_report_rewrites_inside_links_and_warns_about_others(vault: Path) -> None:
    chart = vault / "Assets/chart one.png"
    content = (
        "<html><head></head><body>"
        f'<img src="{chart}"><a href="file://{vault}/Reports/Topic/other.html#s">Other</a>'
        '<a href="/tmp/raticode-test/scratch.png">Temp</a>'
        f"<a href='{OUTSIDE_FILE}'>Outside</a>"
        '<a href="https://example.com">Web</a><a href="#top">Top</a>'
        f"<pre>{chart}</pre></body></html>"
    )
    result = save_report(vault, "Reports/Topic/report.html", content, "html")
    saved = (vault / "Reports/Topic/report.html").read_text()
    assert 'src="../../Assets/chart%20one.png"' in saved
    assert 'href="other.html#s"' in saved
    # Links outside the brain are saved unchanged, and only text inside tags changes.
    assert 'href="/tmp/raticode-test/scratch.png"' in saved
    assert f"href='{OUTSIDE_FILE}'" in saved
    assert 'href="https://example.com"' in saved and 'href="#top"' in saved
    assert f"<pre>{chart}</pre>" in saved
    assert result["linksMadeRelative"] == 2
    assert len(result["warnings"]) == 2
    assert "temporary folder" in result["warnings"][0]
    assert "outside the brain" in result["warnings"][1]


def test_saving_markdown_rewrites_links_but_not_code(vault: Path) -> None:
    chart = vault / "Assets/chart one.png"
    other = (vault / "Reports/Topic/other.html").as_uri()
    content = (
        f"# Notes\n\n![Chart](<{chart}>) and [other]({other})\n"
        f"[ref]: {quote(str(vault))}/Assets/chart%20one.png\n\n"
        f"`[inline]({chart})`\n\n```\n[fenced]({vault}/Assets/x.png)\n```\n"
    )
    result = save_report(vault, "Reports/notes.md", content, "md")
    saved = (vault / "Reports/notes.md").read_text()
    assert "![Chart](<../Assets/chart%20one.png>)" in saved
    assert "[other](Topic/other.html)" in saved
    assert "[ref]: ../Assets/chart%20one.png" in saved
    assert f"`[inline]({chart})`" in saved
    assert f"[fenced]({vault}/Assets/x.png)" in saved
    assert result["linksMadeRelative"] == 3
    assert "warnings" not in result


def test_rewrite_is_idempotent(vault: Path) -> None:
    text = f'<a href="{vault}/Assets/chart one.png">c</a><a href="/tmp/x.png">t</a>'
    once, rewritten, flagged = rewrite_links(text, "a.html", vault, vault / "Reports")
    assert len(rewritten) == 1 and [check.reason for check in flagged] == [TEMPORARY]
    again, rewritten_again, flagged_again = rewrite_links(once, "a.html", vault, vault / "Reports")
    assert again == once and rewritten_again == []
    assert [check.reason for check in flagged_again] == [TEMPORARY]
    assert check_link(rewritten[0].relative, vault, vault / "Reports").reason is None


def link_issues(library: BrainLibrary) -> list[dict[str, Any]]:
    return [item for item in library.snapshot()["health"] if item["kind"] == "link"]


def test_library_health_lists_dead_links_and_repairs_absolute_ones(
    tmp_path: Path, vault: Path
) -> None:
    library = BrainLibrary(tmp_path / "app", start=False)
    try:
        note = vault / "Reports/Topic/note.md"
        note.write_text(
            "# Note\n\n"
            f"[chart](<{vault}/Assets/chart%20one.png>) "
            f"[gone](<{vault}/Reports/gone.md>) "
            "[temp](/tmp/raticode-test/x.png) "
            f"[out]({OUTSIDE_FILE}) [exists]({Path(__file__).resolve()}) "
            "[missing](missing.md) [ok](other.html)\n"
        )
        brain = library.open({"root": str(vault)})
        for task in library.snapshot()["tasks"]:
            assert wait_task(library, task)["state"] == "done"
        reasons = sorted(item["reason"] for item in link_issues(library))
        assert reasons == ["absolute", "missing", "missing", "outside", "temporary"]
        absolute = next(item for item in link_issues(library) if item["reason"] == "absolute")
        assert absolute["fix"] == "Make links relative"
        assert absolute["target"] == f"{vault}/Assets/chart%20one.png"
        assert any(item.get("absolute") for item in link_issues(library))

        before = note.read_text()
        preview = library.request("absolute_links_preview", {"brainId": brain["id"]})
        assert note.read_text() == before  # A preview never writes.
        assert (preview["count"], preview["documents"], preview["missingTargets"]) == (2, 1, 1)
        # The existing outside file is counted but not listed as dead.
        assert (preview["temporary"], preview["outside"]) == (1, 2)
        assert [item["new"] for item in preview["changes"]] == [
            "../../Assets/chart%20one.png",
            "../gone.md",
        ]

        stale = wait_task(
            library,
            library.start_task("repair-absolute-links", {"brainId": brain["id"], "expected": 5}),
        )
        assert stale["state"] == "failed" and note.read_text() == before
        done = wait_task(
            library,
            library.start_task("repair-absolute-links", {"brainId": brain["id"], "expected": 2}),
        )
        assert done["state"] == "done", done
        assert done["result"]["written"] == 1
        repaired = note.read_text()
        # Only the target changes; the <...> around it stays.
        assert "[chart](<../../Assets/chart%20one.png>)" in repaired
        assert "[gone](<../gone.md>)" in repaired
        assert "[temp](/tmp/raticode-test/x.png)" in repaired
        assert f"[out]({OUTSIDE_FILE})" in repaired

        again = library.request("absolute_links_preview", {"brainId": brain["id"]})
        assert again["count"] == 0 and again["temporary"] == 1
        reasons = sorted(item["reason"] for item in link_issues(library))
        assert reasons == ["missing", "missing", "outside", "temporary"]
    finally:
        library.close()


def test_repairing_one_dead_link_rewrites_html_attributes(tmp_path: Path, vault: Path) -> None:
    library = BrainLibrary(tmp_path / "app", start=False)
    try:
        report = vault / "Reports/Topic/report.html"
        report.write_text(
            "<html><head><title>Report</title></head><body>"
            '<img src="/tmp/raticode-test/chart.png"><a href="/tmp/raticode-test/chart.png">c</a>'
            "</body></html>"
        )
        brain = library.open({"root": str(vault)})
        for task in library.snapshot()["tasks"]:
            assert wait_task(library, task)["state"] == "done"
        issue = next(item for item in link_issues(library) if item["reason"] == "temporary")
        receipt = wait_task(
            library,
            library.start_task(
                "repair-link",
                {
                    "brainId": brain["id"],
                    "docId": issue["docId"],
                    "old": issue["target"],
                    "target": "Assets/chart one.png",
                },
            ),
        )
        assert receipt["state"] == "done", receipt
        text = report.read_text()
        assert text.count('="../../Assets/chart%20one.png"') == 2
        assert not [item for item in link_issues(library) if item["docId"] == issue["docId"]]
    finally:
        library.close()

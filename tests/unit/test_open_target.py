"""Open-resource targets can't smuggle options, protocol handlers or extra commands (B-O14)."""

from __future__ import annotations

import pytest

from gofer.utils.open_target import checked_open_target


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("--help", "file"),
        ("-a Calculator", "auto"),
        ("https://example.com/\nsecond", "url"),
        ("file:///etc/passwd", "url"),
        ("ms-msdt:/id PCWDiagnostic", "url"),
        ("javascript:alert(1)", "url"),
    ],
)
def test_unsafe_targets_are_refused(target, kind):
    with pytest.raises(ValueError):
        checked_open_target(target, kind)


@pytest.mark.parametrize(
    ("target", "kind"),
    [
        ("https://github.com/zacharyivie/Raticode", "url"),
        ("HTTP://example.com", "url"),
        ("mailto:someone@example.invalid", "url"),
        ("/home/user/report & notes.html", "file"),
        ("--anything-goes-for-an-app", "app"),
    ],
)
def test_ordinary_targets_still_open(target, kind):
    assert checked_open_target(target, kind) == target

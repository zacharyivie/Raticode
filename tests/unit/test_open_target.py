"""Open-resource targets can't smuggle options, protocol handlers or extra commands (B-O14)."""

from __future__ import annotations

import sys

import pytest

from gofer.core.executor import open_resource_args
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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX opener")
def test_the_opener_never_gets_an_option_and_never_goes_through_cmd():
    assert open_resource_args("/tmp/a b.txt", "file")[-1] == "/tmp/a b.txt"
    assert "cmd" not in open_resource_args("https://example.com", "url")
    with pytest.raises(ValueError):
        open_resource_args("-n", "file")

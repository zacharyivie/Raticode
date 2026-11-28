"""Every test's temp folder has a space and a non-ASCII letter in its path.

Users keep projects and data in folders like "~/Library/Application Support" or
"~/Documents/Projets ñ", so tests run with such a path everywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import SPACED_TEMP_FOLDER


def test_tmp_path_has_a_space_and_a_non_ascii_letter(tmp_path: Path) -> None:
    assert tmp_path.name == SPACED_TEMP_FOLDER
    assert " " in tmp_path.name
    assert any(not letter.isascii() and letter.isalpha() for letter in tmp_path.name)
    assert tmp_path.is_dir()


def test_each_test_gets_its_own_folder_inside_pytests_own(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    # The parent is pytest's per-test folder, so a word an unquoted command splits
    # off the path ("…/Application") lands there and not in a folder other tests share.
    assert tmp_path.parent.parent == tmp_path_factory.getbasetemp()
    assert tmp_path.parent.name != SPACED_TEMP_FOLDER

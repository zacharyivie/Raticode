from __future__ import annotations

from pathlib import Path

from gofer.utils.paths import get_data_dir


def test_default_data_dir_is_isolated(
    isolated_gofer_data_dir: Path,
) -> None:
    assert get_data_dir() == isolated_gofer_data_dir
    assert isolated_gofer_data_dir.name == "gofer"

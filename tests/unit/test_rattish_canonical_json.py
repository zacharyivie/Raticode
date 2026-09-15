from __future__ import annotations

import pytest

from gofer.rattish.contracts import canonical_json_bytes


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-0.0, "0"),
        (1.0, "1"),
        (1e-7, "1e-7"),
        (1e-6, "0.000001"),
        (1e20, "100000000000000000000"),
        (1e21, "1e+21"),
    ],
)
def test_rfc_8785_number_spelling(value: float, expected: str) -> None:
    assert canonical_json_bytes(value).decode("utf-8") == expected


def test_rfc_8785_sorts_object_keys_by_utf16_code_units() -> None:
    value = {"€": "euro", "\r": "cr", "1": "one", "😀": "face", "ö": "o"}

    assert canonical_json_bytes(value).decode("utf-8") == (
        '{"\\r":"cr","1":"one","ö":"o","€":"euro","😀":"face"}'
    )


def test_canonical_json_rejects_non_finite_numbers() -> None:
    with pytest.raises(ValueError, match="non-finite"):
        canonical_json_bytes(float("nan"))

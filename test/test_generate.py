from __future__ import annotations

import pytest

from scripts.generate import WPM_COLUMNS, _field_factory


def test_unitless_boolean_register_is_generated() -> None:
    """A unitless 0..1 register remains a writable Boolean field."""
    row = ["30001", "PUMP", "", "", "", "", "0", "1", "6", "", "", ""]

    assert _field_factory(row, WPM_COLUMNS, writable=True) == "boolean(30000, nan=UNAVAILABLE, writable=True)"


def test_boolean_register_rejects_a_unit() -> None:
    """A 0..1 register with a unit must not silently lose CSV metadata."""
    row = ["30001", "PUMP", "", "", "", "", "0", "1", "6", "°C", "", ""]

    with pytest.raises(ValueError, match=r"0\.\.1 register 'PUMP' cannot declare unit '°C'"):
        _field_factory(row, WPM_COLUMNS, writable=False)

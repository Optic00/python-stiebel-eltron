"""Experimental read-only WPM G API (integration issue pail23/stiebel_eltron_isg_component#684)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from pystiebeleltron.wpmg import WpmGAlarms, WpmGStiebelEltronAPI, WpmGSystemState, WpmGSystemValues

CAPTURE = Path(__file__).parent / "fixtures" / "wpmg_primary_capture.json"
ALARMS = range(8999, 9063)
STATE = range(7499, 7700)


def _capture() -> dict:
    return json.loads(CAPTURE.read_text(encoding="utf-8"))


class RefusingUnit(MockModbusUnit):
    """Answers the public capture, refusing input reads that touch ``refused``."""

    def __init__(self, refused: range | None = None) -> None:
        super().__init__(MockModbusConnection(), 1)
        self.input.update({address: raw for address, _, raw, _ in _capture()["rows"]})
        self.refused = refused

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        values = await super().read_input_registers(address, count)
        if self.refused is not None and any(word in self.refused for word in range(address, address + count)):
            raise IllegalDataAddressError()
        return values


@pytest.mark.asyncio
async def test_public_capture_replays_every_declared_field() -> None:
    capture = _capture()
    rows = capture["rows"]
    by_address = {address: (field, raw, expected) for address, field, raw, expected in rows}
    assert len(rows) == len(by_address) == 149
    assert {field for _, field, _, _ in rows} == {
        f"{component}.{name}"
        for component, model in (
            ("system_values", WpmGSystemValues),
            ("system_state", WpmGSystemState),
            ("alarms", WpmGAlarms),
        )
        for name in model.declared_fields
    }

    unit = RefusingUnit()
    writes = []
    unit.on_write(writes.append)
    api = WpmGStiebelEltronAPI(unit)
    await api.async_update()

    # Same request plan as alpha 3: eleven FC04 reads covering exactly the declared words.
    assert len(unit.read_events) == 11
    assert all(event.register_type == "input" for event in unit.read_events)
    read_addresses = {address for event in unit.read_events for address in range(event.address, event.address + event.count)}
    assert read_addresses == set(by_address)
    assert set(capture["scan_only_addresses"]).isdisjoint(read_addresses)
    assert writes == []
    assert api.dropped_optional_components == ()

    unavailable = 0
    for _, field, _, expected in rows:
        component, name = field.split(".", 1)
        actual = getattr(getattr(api, component), name)
        if expected is None or isinstance(expected, bool):
            assert actual is expected, field
        else:
            assert actual == pytest.approx(expected), field
        unavailable += expected is None
    assert unavailable == 10

    # Explicit capture examples guard the wire offset, signed decoding, and scale.
    assert by_address[6020] == ("system_values.brine_inlet_temperature", 2370, 23.7)
    assert by_address[6105] == ("system_values.supercooling", 65201, -3.35)
    assert by_address[7505] == ("system_state.control_signal_brine_pump", 1, True)
    assert by_address[9046] == ("alarms.level_3_notification_heating_circuit_2_temperature", 1, True)


@pytest.mark.asyncio
async def test_api_is_read_only() -> None:
    unit = RefusingUnit()
    writes = []
    unit.on_write(writes.append)
    api = WpmGStiebelEltronAPI(unit)
    await api.async_update()
    for component in (api.system_values, api.system_state, api.alarms):
        for name in component.declared_fields:
            with pytest.raises(Exception):  # noqa: B017, PT011 - any refusal is fine, as long as nothing is written
                await component.write(name, 0)
    assert writes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("refused", "dropped", "component"), [(ALARMS, ("WpmGAlarms",), "alarms"), (STATE, ("WpmGSystemState",), "system_state")])
async def test_unserved_optional_block_is_dropped_and_stays_unknown(refused: range, dropped: tuple[str, ...], component: str) -> None:
    unit = RefusingUnit(refused)
    api = WpmGStiebelEltronAPI(unit)
    await api.async_update()

    assert api.dropped_optional_components == dropped
    block = getattr(api, component)
    # Unknown, never "no alarm" or "off".
    assert all(getattr(block, name) is None for name in block.declared_fields)
    assert api.system_values.brine_inlet_temperature == pytest.approx(23.7)

    reads_before = len(unit.read_events)
    await api.async_update()
    touched = {address for event in unit.read_events[reads_before:] for address in range(event.address, event.address + event.count)}
    assert touched.isdisjoint(refused)


@pytest.mark.asyncio
async def test_served_optional_block_refused_later_fails_the_poll() -> None:
    unit = RefusingUnit()
    api = WpmGStiebelEltronAPI(unit)
    await api.async_update()
    unit.refused = ALARMS
    with pytest.raises(IllegalDataAddressError):
        await api.async_update()
    assert api.dropped_optional_components == ()


@pytest.mark.asyncio
async def test_refused_required_block_fails_the_poll() -> None:
    api = WpmGStiebelEltronAPI(RefusingUnit(range(6000, 6001)))
    with pytest.raises(IllegalDataAddressError):
        await api.async_update()

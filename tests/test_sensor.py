"""Tests for the sensor entity.

These exist mainly to catch regressions in the entity-to-coordinator binding.
The original implementation inherited from ``CoordinatorEntity`` (which
expects a ``DataUpdateCoordinator`` exposing ``last_update_success``), but
the bluetooth ``ActiveBluetoothDataUpdateCoordinator`` does not provide that
attribute. We bind manually instead, and these tests assert that contract.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.govee_h5086_ble.coordinator import GoveeH5086Coordinator
from custom_components.govee_h5086_ble.parser import PowerReading
from custom_components.govee_h5086_ble.sensor import SENSORS, GoveeH5086Sensor


def _make_sensor(reading: PowerReading | None) -> GoveeH5086Sensor:
    """Build a GoveeH5086Sensor against a spec'd mock coordinator.

    Using ``spec=GoveeH5086Coordinator`` means the mock raises ``AttributeError``
    for any attribute not declared on the real coordinator - that's how this
    test would have caught the ``last_update_success`` regression.
    """
    coordinator = MagicMock(spec=GoveeH5086Coordinator)
    coordinator.address = "AA:BB:CC:DD:EE:FF"
    coordinator.last_reading = reading
    coordinator.entry = SimpleNamespace(title="Test Plug", entry_id="abc")
    return GoveeH5086Sensor(coordinator, SENSORS[0])  # voltage


def test_sensor_native_value_when_reading_present() -> None:
    reading = PowerReading(
        time_on_s=10,
        accum_wh=0.5,
        voltage_v=120.0,
        current_a=0.1,
        power_w=12.0,
        power_factor_pct=70,
    )
    sensor = _make_sensor(reading)
    # First entry in SENSORS is voltage
    assert sensor.native_value == 120.0


def test_sensor_native_value_none_when_no_reading() -> None:
    sensor = _make_sensor(None)
    assert sensor.native_value is None


def test_sensor_does_not_use_data_update_coordinator_entity() -> None:
    """Regression guard: must NOT inherit from helpers.update_coordinator.CoordinatorEntity.

    That base class assumes a DataUpdateCoordinator and calls
    ``coordinator.last_update_success``, which the bluetooth active
    coordinator does not provide.
    """
    from homeassistant.helpers.update_coordinator import CoordinatorEntity

    assert not issubclass(GoveeH5086Sensor, CoordinatorEntity)


def test_sensor_unique_id_includes_address_and_key() -> None:
    sensor = _make_sensor(None)
    assert sensor.unique_id == "AA:BB:CC:DD:EE:FF_voltage"


def test_each_sensor_description_uses_a_callable_value_fn() -> None:
    """Each entity description must extract a number from a PowerReading."""
    reading = PowerReading(
        time_on_s=1,
        accum_wh=2.0,
        voltage_v=3.0,
        current_a=4.0,
        power_w=5.0,
        power_factor_pct=6,
    )
    for description in SENSORS:
        value = description.value_fn(reading)
        assert isinstance(value, (int, float))

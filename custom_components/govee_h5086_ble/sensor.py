"""Sensor entities for the Govee H5086 plug.

Five sensors per device, all sourced from the most recent ``PowerReading``
captured by the coordinator:

- voltage (V, measurement)
- current (A, measurement)
- power (W, measurement)
- power factor (%, measurement)
- energy total (Wh, total_increasing - feeds HA's Energy dashboard)
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import GoveeH5086Coordinator
from .parser import PowerReading

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, kw_only=True)
class GoveeSensorDescription(SensorEntityDescription):
    """Describes one Govee H5086 sensor and how to pull its value from a reading."""

    value_fn: Callable[[PowerReading], float | int]


SENSORS: tuple[GoveeSensorDescription, ...] = (
    GoveeSensorDescription(
        key="voltage",
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=2,
        value_fn=lambda r: r.voltage_v,
    ),
    GoveeSensorDescription(
        key="current",
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=3,
        value_fn=lambda r: r.current_a,
    ),
    GoveeSensorDescription(
        key="power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=2,
        value_fn=lambda r: r.power_w,
    ),
    GoveeSensorDescription(
        key="energy",
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        suggested_display_precision=1,
        value_fn=lambda r: r.accum_wh,
    ),
    GoveeSensorDescription(
        key="power_factor",
        translation_key="power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda r: r.power_factor_pct,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the H5086 sensor entities for a config entry."""
    coordinator: GoveeH5086Coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(GoveeH5086Sensor(coordinator, description) for description in SENSORS)


class GoveeH5086Sensor(SensorEntity):
    """A single sensor pulling its value off the latest PowerReading.

    We bind to ``ActiveBluetoothDataUpdateCoordinator`` manually (rather than
    via ``CoordinatorEntity``) because that helper's base class is
    ``DataUpdateCoordinator``, which exposes ``last_update_success`` - an
    attribute the bluetooth active coordinator does not have. The bluetooth
    coordinator exposes ``async_add_listener`` directly, which is all we need.
    """

    entity_description: GoveeSensorDescription
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: GoveeH5086Coordinator,
        description: GoveeSensorDescription,
    ) -> None:
        self._coordinator = coordinator
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            identifiers={(DOMAIN, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=coordinator.entry.title,
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates once the entity is in HA."""
        await super().async_added_to_hass()
        self.async_on_remove(self._coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        """Coordinator fired an update; re-evaluate native_value & availability."""
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available iff we have a recent reading AND the plug is in BLE range.

        ``async_address_present`` returns True while HA's bluetooth manager is
        still seeing advertisements from this address, which is the signal we
        want: it goes False after the plug stops broadcasting (out of range,
        unplugged, etc.) without needing a full poll-failure threshold.
        """
        if self._coordinator.last_reading is None:
            return False
        return bluetooth.async_address_present(
            self.hass, self._coordinator.address, connectable=True
        )

    @property
    def native_value(self) -> float | int | None:
        reading = self._coordinator.last_reading
        if reading is None:
            return None
        return self.entity_description.value_fn(reading)

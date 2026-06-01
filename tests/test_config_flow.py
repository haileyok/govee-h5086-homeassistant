"""Tests for the Govee H5086 config flow."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.govee_h5086_ble.const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)


def _make_service_info(
    *,
    address: str = "AA:BB:CC:DD:EE:FF",
    name: str = "GVH5086EEFF",
) -> BluetoothServiceInfoBleak:
    """Build a minimal BluetoothServiceInfoBleak the config flow will accept."""
    from unittest.mock import MagicMock

    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData

    ble_device = MagicMock(spec=BLEDevice)
    ble_device.address = address
    ble_device.name = name

    advertisement = MagicMock(spec=AdvertisementData)
    advertisement.local_name = name
    advertisement.manufacturer_data = {}
    advertisement.service_data = {}
    advertisement.service_uuids = []
    advertisement.rssi = -55
    advertisement.tx_power = None

    return BluetoothServiceInfoBleak(
        name=name,
        address=address,
        rssi=-55,
        manufacturer_data={},
        service_data={},
        service_uuids=[],
        source="local",
        device=ble_device,
        advertisement=advertisement,
        connectable=True,
        time=0.0,
        tx_power=None,
    )


async def test_bluetooth_discovery_creates_entry(hass: HomeAssistant) -> None:
    """A GVH5086* advertisement should lead to a config entry."""
    discovery = _make_service_info()

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"

    confirm = await hass.config_entries.flow.async_configure(result["flow_id"], user_input={})
    assert confirm["type"] == FlowResultType.CREATE_ENTRY
    assert confirm["title"] == "GVH5086EEFF"
    assert confirm["data"] == {CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"}


async def test_bluetooth_discovery_rejects_non_h5086(
    hass: HomeAssistant,
) -> None:
    """Other Govee plugs (e.g. H5080) must not match this integration."""
    discovery = _make_service_info(name="ihoment_H5080_1234")

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=discovery,
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_bluetooth_discovery_aborts_when_already_configured(
    hass: HomeAssistant,
) -> None:
    """Re-discovering an already-added plug should abort cleanly."""
    existing = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"},
    )
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_BLUETOOTH},
        data=_make_service_info(),
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_user_flow_picks_discovered_plug(hass: HomeAssistant) -> None:
    """Manual add: a discovered plug appears in the dropdown."""
    with patch(
        "custom_components.govee_h5086_ble.config_flow.async_discovered_service_info",
        return_value=[_make_service_info()],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        created = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"},
        )
        assert created["type"] == FlowResultType.CREATE_ENTRY
        assert created["data"] == {CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"}


async def test_user_flow_aborts_when_nothing_found(hass: HomeAssistant) -> None:
    with patch(
        "custom_components.govee_h5086_ble.config_flow.async_discovered_service_info",
        return_value=[],
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "no_devices_found"


def test_options_flow_does_not_override_init() -> None:
    """Regression guard: HA 2025.12+ removed the OptionsFlow.config_entry setter.

    A custom ``__init__`` that sets ``self.config_entry`` raises
    ``AttributeError: property 'config_entry' ... has no setter`` at runtime.
    The modern pattern is to not override ``__init__`` and rely on the base
    class's property; this test fails if anyone re-introduces the old shape.
    """
    from custom_components.govee_h5086_ble.config_flow import GoveeH5086OptionsFlow

    assert "__init__" not in GoveeH5086OptionsFlow.__dict__, (
        "GoveeH5086OptionsFlow should not override __init__; rely on the base "
        "OptionsFlow class for self.config_entry."
    )


@pytest.mark.parametrize(
    ("user_value", "should_accept"),
    [
        (DEFAULT_SCAN_INTERVAL, True),
        (15, True),
        (3600, True),
        (5, False),  # below MIN_SCAN_INTERVAL
        (5000, False),  # above MAX_SCAN_INTERVAL
    ],
)
async def test_options_flow_validates_scan_interval(
    hass: HomeAssistant, user_value: int, should_accept: bool
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="AA:BB:CC:DD:EE:FF",
        data={CONF_ADDRESS: "AA:BB:CC:DD:EE:FF"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    if should_accept:
        finished = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={CONF_SCAN_INTERVAL: user_value},
        )
        assert finished["type"] == FlowResultType.CREATE_ENTRY
        assert finished["data"] == {CONF_SCAN_INTERVAL: user_value}
    else:
        with pytest.raises(Exception):  # voluptuous.Invalid bubbles up
            await hass.config_entries.options.async_configure(
                result["flow_id"],
                user_input={CONF_SCAN_INTERVAL: user_value},
            )

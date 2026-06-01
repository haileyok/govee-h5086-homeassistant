"""Config + options flows for the Govee H5086 BLE integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

try:
    # HA 2024.4+
    from homeassistant.config_entries import ConfigFlowResult
except ImportError:  # pragma: no cover - compat shim for older HA test pins
    from homeassistant.data_entry_flow import FlowResult as ConfigFlowResult

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .parser import is_h5086_local_name


class GoveeH5086ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup for a single Govee H5086 plug."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        # address -> human-readable label, populated for the user step.
        self._discovered: dict[str, str] = {}

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle a plug surfaced by HA's Bluetooth scanner."""
        if not is_h5086_local_name(discovery_info.name):
            return self.async_abort(reason="not_supported")
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirmation step shown after a Bluetooth discovery."""
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._discovery.name,
                data={CONF_ADDRESS: self._discovery.address},
            )
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery.name,
                "address": self._discovery.address,
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual add: pick from currently-visible H5086 plugs."""
        if user_input is not None:
            address: str = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered.get(address, address),
                data={CONF_ADDRESS: address},
            )

        current_addresses = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in current_addresses:
                continue
            if not is_h5086_local_name(info.name):
                continue
            self._discovered[info.address] = f"{info.name} ({info.address})"

        if not self._discovered:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(self._discovered)}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> OptionsFlow:
        # HA 2025.12+ made ``OptionsFlow.config_entry`` a read-only property
        # provided by the base class. Do NOT pass the entry to the constructor
        # or store it ourselves - the flow manager assigns it.
        return GoveeH5086OptionsFlow()


class GoveeH5086OptionsFlow(OptionsFlow):
    """Lets the user adjust scan_interval after initial setup.

    Intentionally has no ``__init__``: ``OptionsFlow.config_entry`` is provided
    by the base class as a property in modern HA, and attempting to set it
    raises ``AttributeError: property 'config_entry' ... has no setter``.
    """

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                    )
                }
            ),
        )

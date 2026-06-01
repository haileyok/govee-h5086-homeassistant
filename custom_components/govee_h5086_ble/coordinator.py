"""Active-Bluetooth coordinator that polls a Govee H5086 on a configurable cadence.

The H5086 only emits power data via GATT notifications - there is no
on-demand read. To approximate polling without holding a connection open, we:

1. Wait for HA's Bluetooth integration to surface a fresh advertisement
   (proving the device is in range).
2. ``establish_connection`` to the plug.
3. ``start_notify`` on the RECV characteristic.
4. Wait up to ``NOTIFY_TIMEOUT_S`` for the next valid ``ee19`` packet
   (the plug pushes one every ~5 seconds).
5. Disconnect.

Because connections are expensive, ``_needs_poll`` gates the cycle to once
per ``scan_interval`` seconds (default 30s, configurable via options flow).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant

from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GOVEE_RECV_CHAR_UUID,
    GOVEE_SEND_CHAR_UUID,
    NOTIFY_TIMEOUT_S,
)
from .parser import InvalidPacket, PowerReading, decode_notification

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

_LOGGER = logging.getLogger(__name__)


class GoveeH5086Coordinator(ActiveBluetoothDataUpdateCoordinator[PowerReading | None]):
    """Coordinator that connect-polls a Govee H5086 plug for power data."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        ble_device: BLEDevice,
    ) -> None:
        self.entry = entry
        self.address: str = ble_device.address
        self._ble_device = ble_device
        self._last_reading: PowerReading | None = None
        super().__init__(
            hass=hass,
            logger=_LOGGER,
            address=self.address,
            needs_poll_method=self._govee_needs_poll,
            poll_method=self._govee_poll,
            mode=bluetooth.BluetoothScanningMode.ACTIVE,
            connectable=True,
        )

    async def async_init(self) -> None:
        """Async initialiser - hook for future setup work."""
        # Nothing required today; reserved so callers can ``await coordinator
        # .async_init()`` without caring whether work happens here.

    @property
    def scan_interval(self) -> int:
        return int(self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    @property
    def last_reading(self) -> PowerReading | None:
        return self._last_reading

    # IMPORTANT: these helper names are deliberately prefixed with ``_govee_``
    # to avoid colliding with ``ActiveBluetoothDataUpdateCoordinator``'s own
    # internal ``_async_poll`` / ``_needs_poll`` methods. The parent class
    # registers its own ``_async_poll(self)`` (no args) as a hassjob with the
    # debouncer; if we name our subclass method the same, MRO routes the
    # debouncer call to OUR method and crashes because the args don't match.
    def _govee_needs_poll(
        self,
        service_info: BluetoothServiceInfoBleak,
        seconds_since_last_poll: float | None,
    ) -> bool:
        """Decide whether to connect-poll on this advertisement.

        HA wakes us for every advertisement; we want to poll at most every
        ``scan_interval`` seconds, and only when HA is running (don't trigger
        a connect during startup).
        """
        if self.hass.state != CoreState.running:
            return False
        if seconds_since_last_poll is None:
            return True
        return seconds_since_last_poll >= self.scan_interval

    async def _govee_poll(self, service_info: BluetoothServiceInfoBleak) -> PowerReading | None:
        """Connect, read one notification, disconnect, return the reading.

        Emits one INFO-level log line per poll attempt so the cadence and
        outcome are visible in the default HA log (no need to enable DEBUG
        for the whole package). At ``scan_interval=30s`` that's ~2 lines per
        minute per plug; tune the package log level higher if it gets noisy.
        """
        ble_device = service_info.device or bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            _LOGGER.info("Poll [%s]: skipped (no connectable BLE device)", self.address)
            return self._last_reading

        try:
            reading = await _read_one_notification(ble_device)
        except Exception as err:
            _LOGGER.info("Poll [%s]: failed (%s)", self.address, err)
            raise

        if reading is None:
            _LOGGER.info("Poll [%s]: no notification within %.1fs", self.address, NOTIFY_TIMEOUT_S)
            return self._last_reading

        self._last_reading = reading
        _LOGGER.info(
            "Poll [%s]: ok  V=%.2fV  I=%.3fA  P=%.2fW  E=%.1fWh  PF=%d%%",
            self.address,
            reading.voltage_v,
            reading.current_a,
            reading.power_w,
            reading.accum_wh,
            reading.power_factor_pct,
        )
        return self._last_reading


async def _read_one_notification(ble_device: BLEDevice) -> PowerReading | None:
    """Connect, capture the next valid ee19 notification, disconnect."""
    client = await establish_connection(
        BleakClientWithServiceCache,
        ble_device,
        ble_device.name or ble_device.address,
        max_attempts=3,
        use_services_cache=True,
    )

    done: asyncio.Future[PowerReading] = asyncio.get_running_loop().create_future()

    def on_notify(_char, data: bytearray) -> None:
        if done.done():
            return
        try:
            reading = decode_notification(bytes(data))
        except InvalidPacket as err:
            _LOGGER.debug(
                "Discarding undecodable notification from %s: %s (%s)",
                ble_device.address,
                err,
                bytes(data).hex(),
            )
            return
        done.set_result(reading)

    try:
        await client.start_notify(GOVEE_RECV_CHAR_UUID, on_notify)
        # A zero-filled write nudges the device to push a notification
        # sooner; ignored by the plug, observed reliable in practice.
        with contextlib.suppress(BleakError):
            await client.write_gatt_char(GOVEE_SEND_CHAR_UUID, bytes(20), response=False)
        try:
            return await asyncio.wait_for(done, timeout=NOTIFY_TIMEOUT_S)
        except TimeoutError:
            _LOGGER.debug("Timed out waiting for notification from %s", ble_device.address)
            return None
    finally:
        with contextlib.suppress(BleakError, Exception):
            await client.stop_notify(GOVEE_RECV_CHAR_UUID)
        with contextlib.suppress(BleakError, Exception):
            await client.disconnect()


__all__ = ["DOMAIN", "GoveeH5086Coordinator"]

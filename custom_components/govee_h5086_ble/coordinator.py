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
import time
from collections.abc import Callable
from datetime import timedelta
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
from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

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

# How long after the last successful poll do we still consider the plug
# "available"? Sensors flip to unavailable past this threshold. We pad the
# user-chosen scan interval by 3x so a single missed poll doesn't blank the
# UI - polls that succeed two cycles in a row keep things steady.
AVAILABILITY_WINDOW_MULTIPLIER = 3


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
        self._last_reading: PowerReading | None = None
        self._last_successful_poll: float | None = None
        # Guards the BLE connect/read/disconnect from running twice in
        # parallel - the advertisement-driven path and the fallback timer
        # path can both fire at roughly the same time.
        self._poll_lock = asyncio.Lock()
        self._unsub_fallback: Callable[[], None] | None = None
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
        """Register the fallback polling timer.

        The parent ``ActiveBluetoothDataUpdateCoordinator`` only polls in
        response to advertisements. If those stop arriving (adapter
        contention, plug entering a low-power advertising mode, etc.),
        polling silently stalls. This timer fires every ``scan_interval``
        seconds and forces a poll if the parent hasn't already done one
        recently.
        """
        self._unsub_fallback = async_track_time_interval(
            self.hass,
            self._fallback_tick,
            timedelta(seconds=self.scan_interval),
        )

    @callback
    def async_stop_fallback(self) -> None:
        """Cancel the fallback timer (registered for unload)."""
        if self._unsub_fallback is not None:
            self._unsub_fallback()
            self._unsub_fallback = None

    @property
    def scan_interval(self) -> int:
        return int(self.entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    @property
    def last_reading(self) -> PowerReading | None:
        return self._last_reading

    @property
    def seconds_since_last_poll(self) -> float | None:
        """Monotonic seconds since the most recent successful poll, or None."""
        if self._last_successful_poll is None:
            return None
        return time.monotonic() - self._last_successful_poll

    @property
    def is_recently_polled(self) -> bool:
        """True iff a successful poll landed within the availability window."""
        age = self.seconds_since_last_poll
        if age is None:
            return False
        return age < self.scan_interval * AVAILABILITY_WINDOW_MULTIPLIER

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
        """Parent class poll callback - fired when a BT advertisement arrives."""
        ble_device = service_info.device or bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        async with self._poll_lock:
            return await self._poll_once(ble_device, source="adv")

    async def _fallback_tick(self, _now) -> None:
        """Periodic fallback poll that runs even when advertisements stall.

        We skip if a recent successful poll already covered this window
        (avoiding double-polls when advertisements are flowing normally).
        Otherwise we resolve a BLEDevice from HA's bluetooth manager and
        attempt a connect/read just like the advertisement path.
        """
        if self.hass.state != CoreState.running:
            return
        age = self.seconds_since_last_poll
        if age is not None and age < self.scan_interval:
            return

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        async with self._poll_lock:
            await self._poll_once(ble_device, source="timer")
        # The parent class fires async_update_listeners() after its own poll
        # path; our timer-driven path doesn't go through that, so notify
        # entities explicitly. Safe to call unconditionally - listeners just
        # re-render with whatever last_reading currently holds.
        self.async_update_listeners()

    async def _poll_once(self, ble_device: BLEDevice | None, *, source: str) -> PowerReading | None:
        """Connect, read one notification, disconnect, update state.

        Returns the cached ``last_reading`` if anything goes wrong, so the
        parent's stored ``self.data`` doesn't flap between a value and None.
        Logs one INFO line per attempt so the cadence and outcome are
        visible in the default HA log.
        """
        if ble_device is None:
            _LOGGER.info(
                "Poll [%s] (%s): skipped (no connectable BLE device)",
                self.address,
                source,
            )
            return self._last_reading

        try:
            reading = await _read_one_notification(ble_device)
        except Exception as err:
            _LOGGER.info("Poll [%s] (%s): failed (%s)", self.address, source, err)
            return self._last_reading

        if reading is None:
            _LOGGER.info(
                "Poll [%s] (%s): no notification within %.1fs",
                self.address,
                source,
                NOTIFY_TIMEOUT_S,
            )
            return self._last_reading

        self._last_reading = reading
        self._last_successful_poll = time.monotonic()
        _LOGGER.info(
            "Poll [%s] (%s): ok  V=%.2fV  I=%.3fA  P=%.2fW  E=%.1fWh  PF=%d%%",
            self.address,
            source,
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

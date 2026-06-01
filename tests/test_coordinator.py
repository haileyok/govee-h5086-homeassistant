"""Tests for the GoveeH5086Coordinator poll cycle.

We mock ``bleak_retry_connector.establish_connection`` so the coordinator
talks to a fake BleakClient that:
  - records ``start_notify`` / ``write_gatt_char`` calls
  - synthesizes one captured ciphertext on the notify handler
  - tracks ``disconnect`` so we know the connection was torn down
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.govee_h5086_ble.const import GOVEE_RECV_CHAR_UUID
from custom_components.govee_h5086_ble.coordinator import _read_one_notification

# One of the captured ciphertexts from the reverse-engineering session.
SAMPLE_PACKET_HEX = "39fbaa1dd8d048759bbf90517748e6e368ee4067"


class FakeBleakClient:
    """Minimal stand-in for BleakClientWithServiceCache."""

    def __init__(self, *, packet: bytes, push_immediately: bool = True) -> None:
        self._packet = packet
        self._push_immediately = push_immediately
        self._notify_handler: Any = None
        self.notify_started = False
        self.disconnected = False
        self.writes: list[bytes] = []

    async def start_notify(self, _uuid: str, handler: Any) -> None:
        self.notify_started = True
        self._notify_handler = handler
        if self._push_immediately:
            # Run on the next event-loop tick to mimic real notification delivery.
            asyncio.get_running_loop().call_soon(handler, MagicMock(), bytearray(self._packet))

    async def stop_notify(self, _uuid: str) -> None:
        self.notify_started = False

    async def write_gatt_char(self, _uuid: str, data: bytes, response: bool = True) -> None:
        self.writes.append(bytes(data))

    async def disconnect(self) -> None:
        self.disconnected = True


@pytest.fixture
def mock_ble_device() -> MagicMock:
    device = MagicMock()
    device.address = "AA:BB:CC:DD:EE:FF"
    device.name = "GVH5086EEFF"
    return device


async def test_read_one_notification_decodes_packet(
    mock_ble_device: MagicMock,
) -> None:
    """Happy path: connect, get one valid packet, disconnect, return reading."""
    fake_client = FakeBleakClient(packet=bytes.fromhex(SAMPLE_PACKET_HEX))
    with patch(
        "custom_components.govee_h5086_ble.coordinator.establish_connection",
        AsyncMock(return_value=fake_client),
    ):
        reading = await _read_one_notification(mock_ble_device)

    assert reading is not None
    assert reading.voltage_v == pytest.approx(120.05, abs=0.01)
    assert reading.power_w == pytest.approx(43.24, abs=0.01)
    assert reading.power_factor_pct == 96
    assert fake_client.disconnected, "connection should be torn down after read"
    assert fake_client.writes, "coordinator should send the wakeup write"


async def test_read_one_notification_times_out(mock_ble_device: MagicMock) -> None:
    """If no notification arrives in NOTIFY_TIMEOUT_S we get None, not a hang."""
    fake_client = FakeBleakClient(packet=b"", push_immediately=False)
    with (
        patch(
            "custom_components.govee_h5086_ble.coordinator.establish_connection",
            AsyncMock(return_value=fake_client),
        ),
        patch(
            "custom_components.govee_h5086_ble.coordinator.NOTIFY_TIMEOUT_S",
            0.05,
        ),
    ):
        reading = await _read_one_notification(mock_ble_device)

    assert reading is None
    assert fake_client.disconnected
    assert fake_client.notify_started is False  # stop_notify ran


async def test_read_one_notification_ignores_undecodable(
    mock_ble_device: MagicMock,
) -> None:
    """Garbled notifications should be discarded silently; we keep waiting."""
    bogus = b"\x00" * 16 + b"\x68\xee\x40\x00"
    fake_client = FakeBleakClient(packet=bogus)
    with (
        patch(
            "custom_components.govee_h5086_ble.coordinator.establish_connection",
            AsyncMock(return_value=fake_client),
        ),
        patch(
            "custom_components.govee_h5086_ble.coordinator.NOTIFY_TIMEOUT_S",
            0.1,
        ),
    ):
        reading = await _read_one_notification(mock_ble_device)

    assert reading is None  # bogus dropped, timeout fired -> None
    assert fake_client.disconnected


async def test_read_one_notification_subscribes_to_recv_char(
    mock_ble_device: MagicMock,
) -> None:
    """Sanity: we listen on the documented Govee RECV characteristic."""
    fake_client = FakeBleakClient(packet=bytes.fromhex(SAMPLE_PACKET_HEX))

    seen: list[str] = []
    original_start = fake_client.start_notify

    async def spy_start_notify(uuid: str, handler: Any) -> None:
        seen.append(uuid)
        await original_start(uuid, handler)

    fake_client.start_notify = spy_start_notify  # type: ignore[assignment]

    with patch(
        "custom_components.govee_h5086_ble.coordinator.establish_connection",
        AsyncMock(return_value=fake_client),
    ):
        await _read_one_notification(mock_ble_device)

    assert seen == [GOVEE_RECV_CHAR_UUID]

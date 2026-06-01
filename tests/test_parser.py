"""Tests for the H5086 packet decoder.

These exercise the real AES decryption path against ciphertexts captured from
three different physical plugs, so a regression in the PSK or the field
layout will surface immediately.
"""

from __future__ import annotations

import pytest

from custom_components.govee_h5086_ble.parser import (
    InvalidPacket,
    PowerReading,
    decode_notification,
    is_h5086_local_name,
)

# Real ciphertexts captured during the protocol-reverse-engineering session.
# Each tuple is (hex_packet, expected_PowerReading).
KNOWN_PACKETS: list[tuple[str, PowerReading]] = [
    (
        # Plug A481, idle 4W phantom load
        "2e93d26cdde9ceeee2b88c2ad9e8d03368ee4034",
        PowerReading(
            time_on_s=2459,
            accum_wh=2.9,
            voltage_v=119.91,
            current_a=0.05,
            power_w=4.26,
            power_factor_pct=68,
        ),
    ),
    (
        # Plug 0057, active 43W load
        "39fbaa1dd8d048759bbf90517748e6e368ee4067",
        PowerReading(
            time_on_s=12310,
            accum_wh=731.1,
            voltage_v=120.05,
            current_a=0.37,
            power_w=43.24,
            power_factor_pct=96,
        ),
    ),
    (
        # Plug EC11, off (zero current/power, voltage still detected)
        "c85b67fe6b8b434cff7363e21355b7ba68ee40ea",
        PowerReading(
            time_on_s=1278,
            accum_wh=0.0,
            voltage_v=121.83,
            current_a=0.0,
            power_w=0.0,
            power_factor_pct=0,
        ),
    ),
]


@pytest.mark.parametrize(("hex_packet", "expected"), KNOWN_PACKETS)
def test_decode_known_packets(hex_packet: str, expected: PowerReading) -> None:
    """Each captured plug ciphertext should decode to its known reading."""
    result = decode_notification(bytes.fromhex(hex_packet))
    assert result.time_on_s == expected.time_on_s
    assert result.voltage_v == pytest.approx(expected.voltage_v, abs=0.01)
    assert result.current_a == pytest.approx(expected.current_a, abs=0.01)
    assert result.power_w == pytest.approx(expected.power_w, abs=0.01)
    assert result.accum_wh == pytest.approx(expected.accum_wh, abs=0.1)
    assert result.power_factor_pct == expected.power_factor_pct


def test_decode_rejects_wrong_length() -> None:
    with pytest.raises(InvalidPacket, match="20-byte"):
        decode_notification(b"\x00" * 19)


def test_decode_rejects_garbled_block() -> None:
    """A packet whose AES block doesn't decrypt to ee19... must raise."""
    bogus = b"\x00" * 16 + b"\x68\xee\x40\x00"
    with pytest.raises(InvalidPacket, match="!= ee19"):
        decode_notification(bogus)


def test_decode_rejects_implausible_voltage() -> None:
    """A decoded packet with a wildly out-of-range voltage must be rejected.

    We build a fake plaintext (header ``ee19`` + huge voltage = 999.99 V),
    encrypt it under the same PSK, then ensure ``decode_notification``
    refuses the result.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    from custom_components.govee_h5086_ble.const import GOVEE_PSK

    # Build plaintext with voltage_v = 999.99 (raw int = 99999, but max int
    # for 2 bytes BE is 65535 -> use 65000 = 650.00 V which is implausible).
    plaintext = (
        b"\xee\x19"  # header
        + (0).to_bytes(3, "big")  # time_on
        + (0).to_bytes(3, "big")  # accum
        + (65000).to_bytes(2, "big")  # voltage 650.00 V
        + (0).to_bytes(2, "big")  # current
        + (0).to_bytes(3, "big")  # power
        + b"\x00"  # power factor
    )
    assert len(plaintext) == 16
    cipher = Cipher(algorithms.AES(GOVEE_PSK), modes.ECB()).encryptor()
    ciphertext = cipher.update(plaintext) + cipher.finalize()
    packet = ciphertext + b"\x68\xee\x40\x00"

    with pytest.raises(InvalidPacket, match="implausible voltage"):
        decode_notification(packet)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("GVH50861234", True),
        ("GVH5086EEFF", True),
        ("GVH5085A481", False),
        ("ihoment_H5080_1234", False),
        ("", False),
        (None, False),
    ],
)
def test_is_h5086_local_name(name: str | None, expected: bool) -> None:
    assert is_h5086_local_name(name) is expected

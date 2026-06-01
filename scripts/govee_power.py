"""Govee H5086 BLE power-monitoring client.

The H5086 emits an encrypted 20-byte status notification every ~5 seconds over
its custom GATT characteristic. Each notification carries the device's current
voltage, current, power, accumulated energy, and power factor.

Packet layout (20 bytes)
------------------------
- bytes [0:16]   AES-128-ECB ciphertext (one block)
- bytes [16:19]  Plaintext marker, observed to be 0x68 0xee 0x40
- byte  [19]     Trailing tag (one byte; not a simple XOR checksum)

Decrypted plaintext (16 bytes) follows the documented Govee H5080 power-data
format, prefixed with ``ee 19``:
    ee19 <time_on_s:3B BE> <accum_0.1Wh:3B BE> <volt_0.01V:2B BE>
         <amp_0.01A:2B BE> <pow_0.01W:3B BE> <power_factor_pct:1B>

Key derivation
--------------
The AES-128 key is the fixed Govee pre-shared key ``b"MakingLifeSmarte"``.
This was determined empirically across multiple plugs; one fixed key decrypts
every H5086 we tested.
"""
from __future__ import annotations

import dataclasses
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

GOVEE_SERVICE_UUID = "00010203-0405-0607-0809-0a0b0c0d1910"
GOVEE_SEND_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b11"
GOVEE_RECV_CHAR_UUID = "00010203-0405-0607-0809-0a0b0c0d2b10"
PSK = b"MakingLifeSmarte"  # AES-128 key, common to all H5086 plugs


@dataclasses.dataclass
class PowerReading:
    """Decoded power-monitoring sample from an H5086 plug."""

    time_on_s: int           # seconds the plug has been powered (current session)
    accum_wh: float          # cumulative energy delivered, in watt-hours
    voltage_v: float         # RMS line voltage
    current_a: float         # RMS current draw
    power_w: float           # real power
    power_factor_pct: int    # power factor, integer percent (0-100)

    def __str__(self) -> str:
        return (
            f"V={self.voltage_v:6.2f}V  "
            f"I={self.current_a:6.3f}A  "
            f"P={self.power_w:7.2f}W  "
            f"E={self.accum_wh:9.2f}Wh  "
            f"PF={self.power_factor_pct:3d}%  "
            f"uptime={self.time_on_s}s"
        )


class InvalidPacket(ValueError):
    """Raised when a notification can't be decoded as an ee19 power record."""


def _decrypt_block(ciphertext: bytes) -> bytes:
    if len(ciphertext) != 16:
        raise InvalidPacket(f"expected 16 bytes of ciphertext, got {len(ciphertext)}")
    decryptor = Cipher(algorithms.AES(PSK), modes.ECB()).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def decode_notification(raw: bytes) -> PowerReading:
    """Decode a 20-byte H5086 BLE notification into a PowerReading.

    Raises ``InvalidPacket`` for anything that doesn't unwrap to an ee19 record.
    """
    if len(raw) != 20:
        raise InvalidPacket(f"expected 20-byte packet, got {len(raw)}")
    pt = _decrypt_block(raw[:16])
    if pt[0] != 0xEE or pt[1] != 0x19:
        raise InvalidPacket(f"decrypted header {pt[:2].hex()} != ee19")
    return PowerReading(
        time_on_s=int.from_bytes(pt[2:5], "big"),
        accum_wh=int.from_bytes(pt[5:8], "big") / 10.0,
        voltage_v=int.from_bytes(pt[8:10], "big") / 100.0,
        current_a=int.from_bytes(pt[10:12], "big") / 100.0,
        power_w=int.from_bytes(pt[12:15], "big") / 100.0,
        power_factor_pct=pt[15],
    )


def is_h5086_advertisement(local_name: str | None) -> bool:
    return bool(local_name) and local_name.startswith("GVH5086")

"""Decode Govee H5086 BLE status notifications into power readings.

Each H5086 plug emits a 20-byte notification on the RECV characteristic every
~5 seconds while connected. The first 16 bytes are an AES-128-ECB block; the
trailing 4 bytes are a plaintext marker (``68 ee 40``) plus a one-byte tag.

Decrypted plaintext follows the documented H5080 ``ee19`` format:

    ee19 <time_on:3B BE> <accum_0.1Wh:3B BE> <volt_0.01V:2B BE>
         <amp_0.01A:2B BE> <pow_0.01W:3B BE> <pf_pct:1B>

The AES key is the fixed pre-shared key ``b"MakingLifeSmarte"`` shared by
every H5086 we've tested - there's no per-device key derivation.
"""

from __future__ import annotations

import dataclasses

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .const import GOVEE_PSK


@dataclasses.dataclass(frozen=True)
class PowerReading:
    """Decoded power-monitoring sample from an H5086 plug.

    Units:
    - ``time_on_s``: seconds, integer
    - ``accum_wh``: watt-hours, float (resolution 0.1 Wh)
    - ``voltage_v``: volts RMS, float (resolution 0.01 V)
    - ``current_a``: amps RMS, float (resolution 0.01 A)
    - ``power_w``: watts (real power), float (resolution 0.01 W)
    - ``power_factor_pct``: integer percent, 0-100
    """

    time_on_s: int
    accum_wh: float
    voltage_v: float
    current_a: float
    power_w: float
    power_factor_pct: int


class InvalidPacket(ValueError):
    """Raised when a notification can't be decoded as an ee19 power record."""


def _decrypt_first_block(ciphertext: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(GOVEE_PSK), modes.ECB()).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


def decode_notification(raw: bytes) -> PowerReading:
    """Decode a 20-byte H5086 BLE notification into a ``PowerReading``.

    Raises ``InvalidPacket`` if the input is the wrong length or the decrypted
    plaintext does not begin with the ``ee19`` marker. Sanity-checks the
    decoded fields against physically plausible ranges - voltage 50-300 V,
    current 0-20 A, power-factor 0-100 - because a wrong PSK or a torn packet
    typically produces wild values rather than a clean decode error.
    """
    if len(raw) != 20:
        raise InvalidPacket(f"expected 20-byte packet, got {len(raw)}")

    plaintext = _decrypt_first_block(raw[:16])
    if plaintext[0] != 0xEE or plaintext[1] != 0x19:
        raise InvalidPacket(
            f"decrypted header {plaintext[:2].hex()} != ee19; key wrong or packet corrupt"
        )

    reading = PowerReading(
        time_on_s=int.from_bytes(plaintext[2:5], "big"),
        accum_wh=int.from_bytes(plaintext[5:8], "big") / 10.0,
        voltage_v=int.from_bytes(plaintext[8:10], "big") / 100.0,
        current_a=int.from_bytes(plaintext[10:12], "big") / 100.0,
        power_w=int.from_bytes(plaintext[12:15], "big") / 100.0,
        power_factor_pct=plaintext[15],
    )

    # Defence against silent miscoding: a plausible-shape header with garbage
    # bytes shouldn't propagate as a "reading" to HA's energy dashboard.
    if not (50.0 <= reading.voltage_v <= 300.0):
        raise InvalidPacket(f"implausible voltage {reading.voltage_v} V")
    if not (0.0 <= reading.current_a <= 20.0):
        raise InvalidPacket(f"implausible current {reading.current_a} A")
    if not (0 <= reading.power_factor_pct <= 100):
        raise InvalidPacket(f"implausible PF {reading.power_factor_pct}%")
    return reading


def is_h5086_local_name(local_name: str | None) -> bool:
    """True if a BLE advertisement local-name belongs to a Govee H5086."""
    return bool(local_name) and local_name.startswith("GVH5086")

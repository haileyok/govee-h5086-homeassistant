"""Constants for the Govee H5086 BLE integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "govee_h5086_ble"

# GATT layout (shared by every H5086 we've tested)
GOVEE_SERVICE_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d1910"
GOVEE_SEND_CHAR_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b11"
GOVEE_RECV_CHAR_UUID: Final = "00010203-0405-0607-0809-0a0b0c0d2b10"

# AES-128-ECB pre-shared key used by every H5086. The plug encrypts the first
# 16 bytes of each status notification with this key; the trailing 4 bytes are
# a plaintext marker (``68 ee 40``) plus a one-byte tag.
GOVEE_PSK: Final = b"MakingLifeSmarte"

# How long we'll wait for the first valid notification after subscribing.
# The plug pushes one every ~5s, so 8s buys us one full cycle plus margin.
NOTIFY_TIMEOUT_S: Final = 8.0

# Default poll cadence (seconds). Configurable via the options flow.
DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 3600

CONF_SCAN_INTERVAL: Final = "scan_interval"

# Device naming
DEVICE_LOCAL_NAME_PREFIX: Final = "GVH5086"
MANUFACTURER: Final = "Govee"
MODEL: Final = "H5086 Smart Plug Pro"

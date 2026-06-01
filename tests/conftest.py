"""Pytest fixtures for the Govee H5086 BLE integration."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None, None, None]:
    """Auto-enable loading custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def bypass_bluez_history() -> Generator[None, None, None]:
    """Skip the BlueZ history load that HA's bluetooth setup performs.

    On non-Linux hosts (or any test env without DBus), ``bluetooth_adapters``
    raises when probing ``unpack_variants``. The history isn't relevant to our
    tests, so return empty dicts and move on. Patch is applied at the module
    where ``manager.py`` imports the function, not at its definition.
    """
    with patch(
        "homeassistant.components.bluetooth.manager.async_load_history_from_system",
        return_value=({}, {}),
    ):
        yield


@pytest.fixture
def mock_bluetooth() -> Generator[None, None, None]:
    """Stub ``async_ble_device_from_address`` for code paths that resolve a peripheral."""
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=MagicMock(address="AA:BB:CC:DD:EE:FF", name="GVH5086EEFF"),
    ):
        yield

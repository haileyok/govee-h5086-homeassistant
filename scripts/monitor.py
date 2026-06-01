"""Live power monitor for Govee H5086 smart plugs over BLE.

USAGE
-----
    monitor.py                                # monitor every visible H5086
    monitor.py GVH50861234                    # monitor by BLE local name
    monitor.py --mac AA:BB:CC:DD:EE:FE        # monitor by Govee-app MAC
    monitor.py --list                         # list visible plugs and exit

WHY --mac WORKS DIFFERENTLY ON MACOS
------------------------------------
macOS hides each peripheral's real BLE MAC behind an opaque per-app UUID, so
we cannot target a plug by its BLE MAC directly. Instead we match by the
device's broadcast local name (e.g. ``GVH5086EEFF``), whose 4-hex suffix is
the last 16 bits of the *BLE* MAC.

The MAC shown in the Govee app is the plug's *WiFi* MAC, which on the H5086
appears to live one slot below the BLE MAC (e.g. WiFi ``...EE:FE`` <-> BLE
``...EE:FF``). ``--mac`` therefore looks for a name suffix equal to the last
two MAC octets, then tries +1 and -1 in the final octet before giving up.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import sys
import time
from typing import Optional

from bleak import BleakClient, BleakScanner

from govee_power import (
    GOVEE_RECV_CHAR_UUID,
    GOVEE_SEND_CHAR_UUID,
    InvalidPacket,
    decode_notification,
    is_h5086_advertisement,
)

SCAN_SECONDS = 8


def _name_suffix_candidates(mac: str) -> list[str]:
    """Return 4-hex name-suffix candidates derived from a MAC.

    The Govee H5086 broadcasts a local name like ``GVH5086XXXX`` where the
    suffix is the last 16 bits of its *BLE* MAC. The Govee app shows the
    *WiFi* MAC, which on observed devices is one less than the BLE MAC
    in the final octet. We return [exact, +1, -1].
    """
    cleaned = mac.replace(":", "").replace("-", "").upper()
    if len(cleaned) != 12:
        raise ValueError(f"MAC must be 12 hex digits, got {mac!r}")
    upper, lower = cleaned[-4:-2], cleaned[-2:]
    last = int(lower, 16)
    return [
        f"{upper}{last:02X}",
        f"{upper}{(last + 1) & 0xFF:02X}",
        f"{upper}{(last - 1) & 0xFF:02X}",
    ]


async def discover(name: Optional[str] = None) -> dict:
    """Scan briefly and return {address: (BLEDevice, advertised_name, rssi)}."""
    found: dict = {}

    def cb(device, adv):
        local_name = adv.local_name or device.name or ""
        if not is_h5086_advertisement(local_name):
            return
        if name and local_name != name:
            return
        prev = found.get(device.address)
        if prev is None or adv.rssi > prev[2]:
            found[device.address] = (device, local_name, adv.rssi)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(SCAN_SECONDS)
    await scanner.stop()
    return found


async def monitor_one(device, label: str, stop_event: asyncio.Event) -> None:
    """Keep a BLE connection up and stream decoded power readings until stop_event."""
    backoff = 1.0
    while not stop_event.is_set():
        try:
            print(f"[{label}] connecting to {device.address}...", flush=True)
            async with BleakClient(device, timeout=20) as client:
                _ = client.services

                def on_notify(_char, data: bytearray) -> None:
                    raw = bytes(data)
                    try:
                        reading = decode_notification(raw)
                    except InvalidPacket as e:
                        print(f"[{label}] skip: {e} raw={raw.hex()}", flush=True)
                        return
                    ts = time.strftime("%H:%M:%S")
                    print(f"[{label}] {ts}  {reading}", flush=True)

                await client.start_notify(GOVEE_RECV_CHAR_UUID, on_notify)
                print(f"[{label}] subscribed; awaiting readings (~5s cadence).", flush=True)
                backoff = 1.0

                # Idle Govee plugs drop the peer fairly quickly; a periodic
                # zero-filled write keeps the link alive without altering state.
                keepalive = bytes(20)
                while client.is_connected and not stop_event.is_set():
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        pass
                    if not client.is_connected or stop_event.is_set():
                        break
                    with contextlib.suppress(Exception):
                        await client.write_gatt_char(
                            GOVEE_SEND_CHAR_UUID, keepalive, response=False
                        )

                with contextlib.suppress(Exception):
                    await client.stop_notify(GOVEE_RECV_CHAR_UUID)
        except Exception as e:
            print(f"[{label}] connection error: {e!r}; retrying in {backoff:.1f}s", flush=True)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


async def resolve_target(args) -> dict:
    """Return the dict of plugs to monitor based on CLI args."""
    if args.mac:
        suffixes = _name_suffix_candidates(args.mac)
        all_plugs = await discover()
        for suffix in suffixes:
            name = f"GVH5086{suffix}"
            matches = {a: t for a, t in all_plugs.items() if t[1] == name}
            if matches:
                if suffix != suffixes[0]:
                    print(
                        f"Note: matched {name} via MAC-suffix fallback "
                        f"(WiFi MAC ends {suffixes[0]}, BLE name ends {suffix}).",
                        file=sys.stderr,
                    )
                return matches
        print(
            f"No plug found whose name suffix matches {suffixes[0]} (also tried "
            f"{', '.join(suffixes[1:])}). Visible plugs:",
            file=sys.stderr,
        )
        for addr, (_, name, rssi) in all_plugs.items():
            print(f"  {name}  rssi={rssi}  addr={addr}", file=sys.stderr)
        return {}
    return await discover(args.name)


async def main_async(args) -> int:
    found = await resolve_target(args)
    if not found:
        return 1

    if args.list:
        print(f"Found {len(found)} Govee H5086 plug(s):")
        for addr, (_, name, rssi) in sorted(found.items(), key=lambda kv: -kv[1][2]):
            print(f"  {name:20s}  rssi={rssi:>4} dBm  addr={addr}")
        return 0

    print(f"Monitoring {len(found)} plug(s); Ctrl-C to stop.")
    for addr, (_, name, rssi) in sorted(found.items(), key=lambda kv: -kv[1][2]):
        print(f"  - {name}  rssi={rssi} dBm  addr={addr}")
    print()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    tasks = [
        asyncio.create_task(monitor_one(device, name, stop_event))
        for device, name, _ in found.values()
    ]
    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*tasks, return_exceptions=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "name", nargs="?", default=None,
        help="Plug local-name (e.g. GVH50861234). Default: monitor all visible.",
    )
    parser.add_argument(
        "--mac",
        help="Target plug by the MAC shown in the Govee app (e.g. AA:BB:CC:DD:EE:FE).",
    )
    parser.add_argument(
        "--list", action="store_true", help="List visible plugs and exit.",
    )
    args = parser.parse_args()
    if args.name and args.mac:
        parser.error("specify a name OR --mac, not both")
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())

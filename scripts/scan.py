"""Scan BLE neighborhood, print advertisements that match the H5086 / target MAC."""
import asyncio
import sys
from bleak import BleakScanner

TARGET = "AA:BB:CC:DD:EE:FE".lower()  # placeholder; replace with your plug's MAC
SCAN_SECONDS = 12


async def main() -> int:
    print(f"Scanning {SCAN_SECONDS}s for BLE devices (target {TARGET})...")
    seen: dict[str, tuple] = {}

    def detection(device, adv):
        addr = (device.address or "").lower()
        name = adv.local_name or device.name or ""
        is_target = addr == TARGET
        is_govee = name.startswith("GVH5086") or name.startswith("ihoment_H5086")
        if is_target or is_govee:
            mfr = {k: v.hex() for k, v in (adv.manufacturer_data or {}).items()}
            svc = list(adv.service_uuids or [])
            key = addr or name
            seen[key] = (device.address, name, adv.rssi, mfr, svc)

    scanner = BleakScanner(detection_callback=detection)
    await scanner.start()
    await asyncio.sleep(SCAN_SECONDS)
    await scanner.stop()

    if not seen:
        print("No matching devices observed.")
        return 1

    for key, (addr, name, rssi, mfr, svc) in seen.items():
        print(f"  addr={addr}  name={name!r}  rssi={rssi}")
        print(f"    manufacturer_data: {mfr}")
        print(f"    service_uuids: {svc}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

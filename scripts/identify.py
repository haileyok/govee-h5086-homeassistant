"""Connect to every nearby H5086 and read its System ID (0x2A23) to find the BD_ADDR.

macOS hides real BLE MACs behind per-app UUIDs, so we match by reading the
Device Information Service's System ID characteristic, which encodes the MAC.
"""
import asyncio
import sys
from bleak import BleakScanner, BleakClient

TARGET_MAC = "AA:BB:CC:DD:EE:FE".lower().replace(":", "")  # placeholder MAC
SCAN_SECONDS = 10
SYSTEM_ID_UUID = "00002a23-0000-1000-8000-00805f9b34fb"
HARDWARE_REV_UUID = "00002a27-0000-1000-8000-00805f9b34fb"
FIRMWARE_REV_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
MANUFACTURER_UUID = "00002a29-0000-1000-8000-00805f9b34fb"
SERIAL_UUID = "00002a25-0000-1000-8000-00805f9b34fb"
MODEL_UUID = "00002a24-0000-1000-8000-00805f9b34fb"


def decode_system_id_to_mac(raw: bytes) -> str:
    """System ID = 8 bytes: lower 5 bytes of MAC || 0xFFFE || upper 1 byte (LE)."""
    # Per Bluetooth spec, System ID is a 64-bit number stored little-endian:
    #   bytes[0:5] = lower 5 octets of MAC (LSB first)
    #   bytes[5:7] = 0xFFFE
    #   bytes[7]   = top octet of MAC (the OUI's first byte)
    # In practice we just reverse the whole 8-byte value and strip the FF FE.
    val = int.from_bytes(raw, "little")
    # MAC = bottom 5 bytes ORed with top 1 byte shifted up.
    bottom = val & 0xFFFFFFFFFF
    top = (val >> 56) & 0xFF
    mac_int = (top << 40) | bottom
    return f"{mac_int:012x}"


async def probe(device) -> dict | None:
    print(f"  connecting {device.address} ({device.name})...", flush=True)
    try:
        async with BleakClient(device, timeout=15) as client:
            out: dict[str, str] = {"address": device.address, "name": device.name or ""}
            for label, uuid in [
                ("system_id", SYSTEM_ID_UUID),
                ("hardware_rev", HARDWARE_REV_UUID),
                ("firmware_rev", FIRMWARE_REV_UUID),
                ("manufacturer", MANUFACTURER_UUID),
                ("serial", SERIAL_UUID),
                ("model", MODEL_UUID),
            ]:
                try:
                    raw = await client.read_gatt_char(uuid)
                    out[label] = raw.hex()
                    if label in ("hardware_rev", "firmware_rev", "manufacturer", "serial", "model"):
                        try:
                            out[label + "_text"] = raw.decode("utf-8", errors="replace")
                        except Exception:
                            pass
                except Exception as e:
                    out[label + "_err"] = repr(e)
            if "system_id" in out:
                out["mac_from_system_id"] = decode_system_id_to_mac(bytes.fromhex(out["system_id"]))
            return out
    except Exception as e:
        print(f"    connect failed: {e}")
        return None


async def main() -> int:
    print(f"Scanning {SCAN_SECONDS}s for H5086 devices...")
    devices_by_addr = {}

    def detection(device, adv):
        name = adv.local_name or device.name or ""
        if name.startswith("GVH5086") or name.startswith("ihoment_H5086"):
            devices_by_addr[device.address] = device

    scanner = BleakScanner(detection_callback=detection)
    await scanner.start()
    await asyncio.sleep(SCAN_SECONDS)
    await scanner.stop()

    if not devices_by_addr:
        print("No H5086 devices found.")
        return 1

    print(f"\nFound {len(devices_by_addr)} candidate(s). Probing each...\n")
    results = []
    for device in devices_by_addr.values():
        info = await probe(device)
        if info:
            results.append(info)
            for k, v in info.items():
                print(f"    {k}: {v}")
            print()

    print("\n=== Summary ===")
    matched = None
    for r in results:
        mac = r.get("mac_from_system_id", "")
        marker = ""
        if mac == TARGET_MAC:
            marker = "  <-- TARGET"
            matched = r
        print(f"  {r.get('name')!r:30s} addr={r['address']}  mac={mac}{marker}")

    if matched:
        print(f"\nMatched! macOS UUID for target {TARGET_MAC}: {matched['address']}")
        return 0
    print(f"\nNo device matched target MAC {TARGET_MAC}.")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

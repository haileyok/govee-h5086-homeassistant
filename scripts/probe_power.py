"""Probe each H5086: send AA00 (power query) WITHOUT auth and capture any response.

Also send AA06 (firmware) and AA07 (hardware) per the H5086 protocol docs.
This tells us whether power data is readable without going through pairing.
"""
import asyncio
import sys
from bleak import BleakScanner, BleakClient

SEND_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"
RECV_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b10"


def xor_cs(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs & 0xFF


def build_pkt(cmd: bytes, payload: bytes = b"") -> bytes:
    """Govee packet = 2-byte cmd + 17-byte payload (zero-padded) + 1-byte XOR checksum."""
    assert len(cmd) == 2
    body = cmd + payload.ljust(17, b"\x00")
    return body + bytes([xor_cs(body)])


async def probe(device, label: str):
    print(f"\n--- {label}: {device.name} ({device.address}) ---")
    notifs: list[bytes] = []

    def on_notify(_, data: bytearray):
        notifs.append(bytes(data))
        print(f"    NOTIFY {bytes(data).hex()}")

    try:
        async with BleakClient(device, timeout=15) as client:
            await client.start_notify(RECV_CHAR, on_notify)

            for cmd_name, pkt in [
                ("AA00 power-read", build_pkt(b"\xaa\x00")),
                ("AA01 state-read", build_pkt(b"\xaa\x01")),
                ("AA06 firmware-v", build_pkt(b"\xaa\x06")),
                ("AA07 hardware-v", build_pkt(b"\xaa\x07")),
                ("AA20 fw-alt-1",   build_pkt(b"\xaa\x20")),
                ("AA21 fw-alt-2",   build_pkt(b"\xaa\x21")),
                ("AAB1 auth-key",   build_pkt(b"\xaa\xb1")),
            ]:
                print(f"  SEND {cmd_name}: {pkt.hex()}")
                try:
                    await client.write_gatt_char(SEND_CHAR, pkt, response=False)
                except Exception as e:
                    print(f"    write failed: {e}")
                    continue
                await asyncio.sleep(0.8)

            await client.stop_notify(RECV_CHAR)
    except Exception as e:
        print(f"  connect/run failed: {e}")
    return notifs


async def main() -> int:
    print("Scanning 8s for H5086 plugs...")
    found = {}

    def cb(device, adv):
        name = adv.local_name or device.name or ""
        if name.startswith("GVH5086"):
            found[device.address] = (device, adv.rssi)

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(8)
    await scanner.stop()

    # Sort by RSSI (strongest first) for stable ordering
    devices = sorted(found.values(), key=lambda t: -t[1])
    print(f"Found {len(devices)} plug(s).")
    for device, rssi in devices:
        await probe(device, f"rssi={rssi}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

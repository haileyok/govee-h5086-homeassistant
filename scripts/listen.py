"""Long passive listen on a single H5086 to learn its notification patterns.

Connects, subscribes to notify, listens 10s with no commands, then sends each
documented command one at a time with 4s wait, capturing all notifications.
"""
import asyncio
import sys
import time
from bleak import BleakScanner, BleakClient

SEND_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b11"
RECV_CHAR = "00010203-0405-0607-0809-0a0b0c0d2b10"


def xor_cs(data: bytes) -> int:
    cs = 0
    for b in data:
        cs ^= b
    return cs & 0xFF


def build_pkt(cmd: bytes, payload: bytes = b"") -> bytes:
    body = cmd + payload.ljust(17, b"\x00")
    return body + bytes([xor_cs(body)])


async def main() -> int:
    name_filter = sys.argv[1] if len(sys.argv) > 1 else "GVH50861234"
    print(f"Looking for {name_filter}...")
    found = {}

    def cb(device, adv):
        n = adv.local_name or device.name or ""
        if n == name_filter:
            found[device.address] = device

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(6)
    await scanner.stop()
    if not found:
        print("not found")
        return 1
    device = next(iter(found.values()))
    print(f"Connecting to {device.name} {device.address}")

    notifs: list[tuple[float, bytes]] = []
    t0 = time.time()

    def on_notify(_, data):
        notifs.append((time.time() - t0, bytes(data)))
        print(f"  [{time.time() - t0:6.2f}s] NOTIFY {bytes(data).hex()}")

    async with BleakClient(device, timeout=15) as client:
        await client.start_notify(RECV_CHAR, on_notify)
        print(f"[{time.time() - t0:6.2f}s] subscribed; listening 10s with no commands...")
        await asyncio.sleep(10)

        commands = [
            ("AAB1 auth-key", build_pkt(b"\xaa\xb1")),
            ("AA00 power-data", build_pkt(b"\xaa\x00")),
            ("AA01 state", build_pkt(b"\xaa\x01")),
            ("AA06 firmware", build_pkt(b"\xaa\x06")),
            ("AA07 hardware-v", build_pkt(b"\xaa\x07")),
        ]

        for name, pkt in commands:
            print(f"\n[{time.time() - t0:6.2f}s] SEND {name}: {pkt.hex()}")
            try:
                await client.write_gatt_char(SEND_CHAR, pkt, response=False)
            except Exception as e:
                print(f"  write err: {e}")
            await asyncio.sleep(4)

        await client.stop_notify(RECV_CHAR)

    print(f"\nTotal notifications: {len(notifs)}")
    for ts, data in notifs:
        print(f"  {ts:6.2f}s {data.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

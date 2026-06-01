"""Capture ALL notifications from one H5086 over a long window, keeping connection alive.

We pace writes to prevent the device from dropping us, and dump every notification
so we can analyze patterns (periodic, encrypted, etc.).
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


def pkt(cmd: bytes, payload: bytes = b"") -> bytes:
    body = cmd + payload.ljust(17, b"\x00")
    return body + bytes([xor_cs(body)])


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "GVH50861234"
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    print(f"Listening to {name} for {seconds}s")

    found = {}

    def cb(device, adv):
        n = adv.local_name or device.name or ""
        if n == name:
            found[device.address] = device

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(6)
    await scanner.stop()
    if not found:
        print("not found")
        return 1

    device = next(iter(found.values()))
    print(f"Connecting to {device.address}")

    t0 = time.time()
    notifs: list[tuple[float, bytes]] = []

    def on_notify(_, data):
        ts = time.time() - t0
        notifs.append((ts, bytes(data)))
        print(f"  [{ts:6.2f}s] RX {bytes(data).hex()}")

    async with BleakClient(device, timeout=20) as client:
        # Be sure services are ready before any I/O
        _ = client.services
        await client.start_notify(RECV_CHAR, on_notify)
        print(f"[{time.time() - t0:6.2f}s] subscribed")

        # Send a single AAB1 right away — many Govee devices expect a probe early
        first = pkt(b"\xaa\xb1")
        print(f"[{time.time() - t0:6.2f}s] TX (AAB1): {first.hex()}")
        try:
            await client.write_gatt_char(SEND_CHAR, first, response=False)
        except Exception as e:
            print(f"  initial write err: {e}")

        # Then just wait, periodically writing the same probe to keep the link up
        end = time.time() + seconds
        while time.time() < end:
            await asyncio.sleep(3)
            if not client.is_connected:
                print(f"[{time.time() - t0:6.2f}s] disconnected, breaking")
                break
            try:
                await client.write_gatt_char(SEND_CHAR, first, response=False)
                print(f"[{time.time() - t0:6.2f}s] TX keepalive AAB1")
            except Exception as e:
                print(f"[{time.time() - t0:6.2f}s] write err: {e}")
                break

        try:
            await client.stop_notify(RECV_CHAR)
        except Exception:
            pass

    print(f"\nGot {len(notifs)} notifications.")
    # Analysis: tail bytes
    print("\nByte-offset entropy (last byte and trailer):")
    if notifs:
        # show columns
        for ts, d in notifs:
            print(f"  {ts:6.2f}s  head={d[:4].hex()}  mid={d[4:16].hex()}  tail={d[16:].hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

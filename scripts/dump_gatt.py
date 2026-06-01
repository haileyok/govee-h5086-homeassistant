"""Connect to each H5086 and dump its full GATT service tree + try reading each readable char."""
import asyncio
import sys
from bleak import BleakScanner, BleakClient


async def dump(device):
    print(f"\n=== {device.name} ({device.address}) ===")
    try:
        async with BleakClient(device, timeout=15) as client:
            for svc in client.services:
                print(f"  service {svc.uuid}  ({svc.description})")
                for ch in svc.characteristics:
                    props = ",".join(ch.properties)
                    print(f"    char {ch.uuid}  [{props}]  ({ch.description})")
                    if "read" in ch.properties:
                        try:
                            data = await client.read_gatt_char(ch.uuid)
                            print(f"      read = {data.hex()}  (text={data!r})")
                        except Exception as e:
                            print(f"      read failed: {e}")
                    for desc in ch.descriptors:
                        try:
                            data = await client.read_gatt_descriptor(desc.handle)
                            print(f"      desc {desc.uuid} = {data.hex()}")
                        except Exception as e:
                            print(f"      desc {desc.uuid} read failed: {e}")
    except Exception as e:
        print(f"  connect failed: {e}")


async def main() -> int:
    print("Scanning 8s for H5086 plugs...")
    found = {}

    def cb(device, adv):
        name = adv.local_name or device.name or ""
        if name.startswith("GVH5086"):
            found[device.address] = device

    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(8)
    await scanner.stop()

    print(f"Found {len(found)}")
    for d in found.values():
        await dump(d)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

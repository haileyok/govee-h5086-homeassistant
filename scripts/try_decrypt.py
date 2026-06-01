"""Try AES-128-ECB decryption on captured notifications.

Per the Govee BLE encryption scheme described in GoveeBTTempLogger:
- 16-byte AES-128-ECB block over bytes [0:16] of the 20-byte packet
- Key = MAC address reversed, padded to 16 bytes (various padding schemes)
- Bytes [16:18] = plaintext marker, byte 19 = XOR checksum

We don't know the BLE MACs of the discoverable plugs (macOS hides them), but we
DO have a user-supplied MAC for the target plug. We try decryption against
captured notifications from all three plugs; the correct one should yield
meaningful, structured plaintext.
"""
import sys
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

USER_MAC_HEX = "AABBCCDDEEFE"  # placeholder; replace with your plug's app-shown MAC

# Captured notifications from each plug (from capture.py / probe_power.py)
CAPTURES = {
    "plug_A": [
        "1c4e93dd4d32300f10154e369d1cf04668ee40c0",
        "2e93d26cdde9ceeee2b88c2ad9e8d03368ee4034",
        "cabcf0cef97782f36cddfdb94aee2d8368ee4009",
        "db2cf5c31f36612e7afc855065a6938668ee404e",
        "585ce6bb47d09ff9f21239974df10b6268ee4060",
        "7042400b82d626653ffd69d5ade5558b68ee4038",
        "201da1c6fc3d7032b642119459f7179468ee4052",
        "eb04ece8778a8e1bbb88230fce4bb75d68ee405c",
        "0880f6f51dfcc2f0603e321add1ce2d068ee405d",
    ],
    "plug_B": [
        "39fbaa1dd8d048759bbf90517748e6e368ee4067",
    ],
    "plug_C": [
        "c85b67fe6b8b434cff7363e21355b7ba68ee40ea",
    ],
}


def make_keys(mac_hex: str) -> list[tuple[str, bytes]]:
    mac = bytes.fromhex(mac_hex)
    rev = mac[::-1]
    return [
        ("mac forward + zero pad",        mac + b"\x00" * (16 - len(mac))),
        ("mac reversed + zero pad",       rev + b"\x00" * (16 - len(rev))),
        ("mac reversed twice (12B) + zp", (rev + mac) + b"\x00" * (16 - 12)),
        ("mac reversed * (16/6) repeat",  (rev * 3)[:16]),
        ("mac forward * (16/6) repeat",   (mac * 3)[:16]),
        ("zero pad + mac",                b"\x00" * (16 - len(mac)) + mac),
        ("zero pad + reversed mac",       b"\x00" * (16 - len(mac)) + rev),
        ("MakingLifeSmarte (pre-shared)", b"MakingLifeSmarte"),
    ]


def try_decrypt(packet_hex: str, key: bytes) -> bytes:
    ct = bytes.fromhex(packet_hex)[:16]
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    dec = cipher.decryptor()
    return dec.update(ct) + dec.finalize()


def is_plausible(pt: bytes) -> tuple[bool, str]:
    """A real plaintext would likely start with a known command byte (0xaa, 0xee, 0x33)
    or contain a low-entropy mix of zeros and small integers. Score crudely."""
    starts_known = pt[0] in (0xAA, 0xEE, 0x33, 0xBB)
    zeros = pt.count(0)
    reasons = []
    if starts_known:
        reasons.append(f"starts with known byte 0x{pt[0]:02x}")
    if zeros >= 4:
        reasons.append(f"{zeros} zero bytes")
    return (starts_known or zeros >= 6), "; ".join(reasons) or "no signal"


def main() -> int:
    print(f"Target MAC: {USER_MAC_HEX}")
    keys = make_keys(USER_MAC_HEX)
    print(f"Trying {len(keys)} key derivations:\n")

    for label, key in keys:
        print(f"=== KEY: {label} ({key.hex()}) ===")
        for device_name, packets in CAPTURES.items():
            best = None
            for packet in packets[:3]:
                try:
                    pt = try_decrypt(packet, key)
                except Exception as e:
                    print(f"  {device_name}: decrypt err {e}")
                    continue
                plausible, reason = is_plausible(pt)
                marker = "  <-- maybe?" if plausible else ""
                if best is None or plausible:
                    best = (packet, pt, reason, marker)
            if best:
                packet, pt, reason, marker = best
                print(f"  {device_name}: ct={packet[:32]}... -> pt={pt.hex()} ({reason}){marker}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())

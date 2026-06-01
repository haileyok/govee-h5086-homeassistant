"""Decode the captured ciphertexts into power readings."""
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

KEY = b"MakingLifeSmarte"

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
    "plug_B": ["39fbaa1dd8d048759bbf90517748e6e368ee4067"],
    "plug_C": ["c85b67fe6b8b434cff7363e21355b7ba68ee40ea"],
}


def decrypt_block(ct16: bytes) -> bytes:
    return Cipher(algorithms.AES(KEY), modes.ECB()).decryptor().update(ct16)


def decode_ee19(pt: bytes) -> dict:
    # ee19 <time:3B BE> <accum_0.1Wh:3B BE> <volt_0.01V:2B BE> <amp_0.01A:2B BE> <pow_0.01W:3B BE> <pf%:1B>
    assert pt[0] == 0xEE and pt[1] == 0x19, f"unexpected header {pt[:2].hex()}"
    time_on_s = int.from_bytes(pt[2:5], "big")
    accum_wh = int.from_bytes(pt[5:8], "big") / 10.0
    voltage = int.from_bytes(pt[8:10], "big") / 100.0
    current = int.from_bytes(pt[10:12], "big") / 100.0
    power = int.from_bytes(pt[12:15], "big") / 100.0
    pf = pt[15]
    return {
        "time_on_s": time_on_s,
        "accum_wh": accum_wh,
        "voltage_v": voltage,
        "current_a": current,
        "power_w": power,
        "power_factor_pct": pf,
        "tail_bytes": pt[16:].hex(),
    }


def main():
    for name, packets in CAPTURES.items():
        print(f"\n=== {name} ({len(packets)} sample{'s' if len(packets) != 1 else ''}) ===")
        for idx, hex_pkt in enumerate(packets):
            raw = bytes.fromhex(hex_pkt)
            pt = decrypt_block(raw[:16]) + raw[16:]
            d = decode_ee19(pt)
            print(f"  #{idx}: V={d['voltage_v']:6.2f}  I={d['current_a']:5.3f}  P={d['power_w']:7.2f}W  E={d['accum_wh']:7.1f}Wh  PF={d['power_factor_pct']:3d}%  t_on={d['time_on_s']:>6}s  ext={d['tail_bytes']}")


if __name__ == "__main__":
    main()

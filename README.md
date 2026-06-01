# Govee H5086 Smart Plug (Bluetooth) for Home Assistant

A HACS-installable custom integration that reads live power-monitoring data
from **Govee H5086** ("Smart Plug Pro with Energy Monitoring") plugs over
Bluetooth Low Energy.

The H5086 broadcasts voltage / current / real-power / power-factor /
accumulated-energy data as encrypted GATT notifications every ~5 seconds.
Govee's official cloud API does not expose any of these values, so this
integration is the only way to get them into Home Assistant locally — no
account, no cloud round-trip, no Govee app polling.

## Sensors exposed

Per plug:

| Sensor          | Device class    | State class          | Unit |
|-----------------|-----------------|----------------------|------|
| Voltage         | `voltage`       | `measurement`        | V    |
| Current         | `current`       | `measurement`        | A    |
| Power           | `power`         | `measurement`        | W    |
| Power factor    | `power_factor`  | `measurement`        | %    |
| Energy          | `energy`        | `total_increasing`   | Wh   |

`Energy` reports the plug's lifetime accumulated watt-hour counter and uses
`total_increasing` state class so it shows up in HA's **Energy** dashboard
without any extra configuration.

## Installation (HACS)

1. In HACS, add this repo as a **Custom repository** (category: *Integration*).
2. Install **Govee H5086 (Bluetooth)**.
3. Restart Home Assistant.
4. HA's Bluetooth scanner should auto-discover any in-range H5086s — accept
   the discovery card. Otherwise add one manually from **Settings → Devices &
   Services → Add Integration → Govee H5086 (Bluetooth)**.

## Configuration

The only knob is **Poll interval** (default 30 seconds, range 10 – 3600s),
adjustable per device via **Configure** on the integration tile. Each poll
opens a fresh BLE connection, captures one notification, and disconnects;
the device does not support on-demand reads.

A 30s interval gives ~120 readings/hour and uses roughly 1 BLE connection
slot every 30s. Lower it if you need finer resolution for the Energy
dashboard or for real-time appliance debugging; raise it if you're hitting
adapter contention from many BLE devices.

## How it works

Each H5086 emits a 20-byte GATT notification on a Govee custom service
(`…0d1910` / `…0d2b10`). Bytes 0–15 are an AES-128-ECB block whose plaintext
follows the documented H5080 `ee19` power record format. The decryption key
is the fixed pre-shared key `b"MakingLifeSmarte"` shared by every H5086 we've
tested — no pairing or per-device key derivation needed.

See [`scripts/`](./scripts/) for the original reverse-engineering tools used
to derive this, and `custom_components/govee_h5086_ble/parser.py` for the
production decoder.

## Development

```sh
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                # unit + integration tests
.venv/bin/ruff check .          # lint
.venv/bin/ruff format --check . # format check
```

CI runs `ruff`, `pytest`, `hassfest`, and HACS validation on every PR — see
`.github/workflows/ci.yml`.

## Limitations

- Read-only. No on/off switch entity (would require a one-time button-press
  pairing flow per plug).
- macOS hides BLE MAC addresses; if you're testing the local CLI in
  `scripts/`, target plugs by their `GVH5086XXXX` broadcast name rather
  than by MAC. HA on Linux doesn't have this restriction.
- The plug pushes data only via GATT notifications — there's no on-demand
  read — so each poll cycle costs one BLE connection. Picking a longer
  poll interval costs you resolution but saves radio time.

## License

MIT — see `LICENSE`.

# CZone Emulator

Linux-first, headless CZone-style switch interface emulator for NMEA 2000/CAN networks.

The application is designed to run without any desktop display stack: no windows, no Tkinter, and no Windows DLL backend. Runtime interaction is provided through the built-in Flask web server and JSON API.

## What it does

At runtime, the emulator:

- Opens a Linux SocketCAN interface using `python-can`.
- Optionally brings the CAN interface up at 250 kbit/s before opening it.
- Opens a Modbus RTU serial connection to poll and command breaker devices.
- Publishes NMEA 2000 identity, heartbeat, and detailed CZone status frames.
- Maintains virtual switch states and adjustable output-current values.
- Reacts to configured CZone circuit codes from any network device and maps each circuit to one or more local loads.
- Exposes a small Flask web UI plus JSON endpoints for monitoring and control.

## Requirements

- Linux with SocketCAN support.
- Python 3.10 or newer.
- `iproute2` (`ip link ...`) for CAN interface status/setup.
- Access to a SocketCAN interface, default: `awlink0`.
- Access to a Modbus RTU serial device, default: `/dev/ttyAS3`.

Python packages:

```bash
pip install flask pyserial python-can
```

## Installation

```bash
git clone <your-repo-url>
cd czone_emulator
python -m venv .venv
source .venv/bin/activate
pip install flask pyserial python-can
```

Ensure the Linux user running the emulator has permission to access the CAN network setup and serial device. Depending on your image, this may require running as root, granting `CAP_NET_ADMIN`, or adding the user to the serial device group such as `dialout`.

## Running

```bash
python czone_emulator.py
```

The Flask server starts automatically because headless mode is the only supported runtime mode.

Default services and devices:

- Web UI/API: `http://0.0.0.0:8080/`
- CAN interface: `awlink0`
- CAN bitrate used for auto setup: `250000`
- Modbus serial port: `/dev/ttyAS3`
- Modbus serial baudrate: `115200`

A typical explicit Linux startup looks like this:

```bash
export CAN_CHANNEL=awlink0
export SERIAL_PORT=/dev/ttyAS3
export SERIAL_BAUDRATE=115200
export WEB_HOST=0.0.0.0
export WEB_PORT=8080
python czone_emulator.py
```

Then open:

```text
http://<host>:8080/
```

## Configuration

### CAN settings

- `CAN_CHANNEL`: SocketCAN interface name. Default: `awlink0`.
- `CAN_AUTO_UP`: Automatically reset/configure/bring up the CAN interface before opening it. Default: `1`. Disable with `0`, `false`, or `no`.
- `CAN_BITRATE`: Bitrate used when `CAN_AUTO_UP` configures the link. Default: `250000`.
- `CAN_SEND_TIMEOUT_SECONDS`: SocketCAN send timeout. Default: `0.2`.
- `CAN_SEND_RETRY_DELAY_SECONDS`: Retry delay when SocketCAN reports ENOBUFS. Default: `0.05`.
- `CAN_SEND_MAX_RETRIES`: Maximum ENOBUFS retries per CAN frame. Default: `40`.

### Serial/Modbus settings

- `SERIAL_PORT`: Serial port for Modbus RTU. Default: `/dev/ttyAS3`.
- `SERIAL_BAUDRATE`: Serial baudrate. Default: `115200`.
- `SERIAL_LINUX_DEFAULT_PORT`: Compatibility fallback used when `SERIAL_PORT=COM8`. Default: `/dev/ttyAS3`.
- `SERIAL_COM_ALIAS_MAP`: Optional comma-separated compatibility alias map, for example `COM8=/dev/ttyAS3,COM9=/dev/ttyUSB0`.
- `MODBUS_INTER_DEVICE_COMMAND_DELAY_SECONDS`: Delay between Modbus write commands when one CZone action maps to multiple RS485 breaker devices. Default: `0.5` (500 ms). Set to `0` to disable the additional command spacing.

### Circuit/load mappings

Incoming PGN 65280 commands are matched by CZone circuit code, not by keyboard CZone ID or key scan. The default in-code `CIRCUIT_LOAD_MAPS` table maps circuits to local loads and accepts either a single load integer or an iterable of load integers:

```python
CIRCUIT_LOAD_MAPS = {
    0x07: 1,
    0x08: 2,
    0x09: 3,
    0x0A: 4,
}
```

At startup, the emulator looks for `configuration.zcf` in the current working directory. When the file exists, it decodes the output-device mappings for the CZone ID represented by `CZONE_DIP_SWITCH_DEFAULT` (the stored DIP byte with its bits reversed, so the default `2` is CZone ID `01000000`) and replaces `CIRCUIT_LOAD_MAPS` with the Circuit ID -> output number relationships from the file. The Web UI shows the active mapping status plus the configuration file's stored filename, decoded internal ZCF name, and size. If `configuration.zcf` is missing, cannot be decoded, or does not contain mappings for that CZone ID, the emulator keeps the built-in table above and reports the fallback in the Web UI mapping card. Circuit codes do not need to match the switch/output number. For example, this is valid and still sends switch/output feedback as outputs 1 and 2:

```python
CIRCUIT_LOAD_MAPS = {
    0x10: 1,
    0x11: 2,
}
```

### Network feedback mapping

Feedback sent by the emulator is keyed to the same local output/load number selected by `CIRCUIT_LOAD_MAPS`; the circuit code itself is not written into the feedback payload. Output ON/OFF feedback keeps the original switch status encoding: switch codes `0x05` through `0x08` represent outputs 1 through 4. Detailed PGN 130817 current feedback also keeps the original byte layout; circuit mapping only decides which local output changes, not how switch status or current feedback is encoded.

Incoming PGN 65280 switch commands still support the existing staged CZone sequence (`0xF1`/`0xF2` followed by `0x40`/`0x42`). Reception/display panels that send direct commands are also supported: command `0x61` switches the mapped output ON and command `0x62` switches it OFF.

### Web settings

- `WEB_HOST`: Flask bind host. Default: `0.0.0.0`.
- `WEB_PORT`: Flask bind port. Default: `8080`.

## Web UI and HTTP API

### `GET /`

Returns the lightweight monitoring/control web page.

### `GET /api/state`

Returns switch states, DIP switch value, configured adjustable output currents, effective reported output currents, and circuit-to-load mappings.

### `POST /api/toggle`

Toggles a switch and returns the updated state.

Request body:

```json
{ "switch_id": 1 }
```

Valid `switch_id` values are `1` through `4`.

### `POST /api/output_current`

Sets an adjustable output current. Values are quantized to 0.1 A and clamped to the supported single-byte range. The configured value is retained while an output is OFF, but detailed current feedback reports `0.0 A` for OFF outputs and reports the configured value when the output is ON.

Request body:

```json
{ "output_index": 1, "amps": 3.5 }
```

Valid `output_index` values are `1` through `4`.

### `POST /api/output_currents`

Sets multiple adjustable output currents in one request. The web UI uses this endpoint when **Apply currents** submits the values currently entered in the output current fields.

Request body:

```json
{ "amps": { "1": 3.5, "2": 3.5, "3": 3.5, "4": 3.5 } }
```

### `POST /api/config/upload`

Uploads a CZone configuration file from the web UI and saves it as `configuration.zcf` in the current working directory of the running process. Only files with the `.zcf` extension are accepted. After a successful upload, the emulator reports the decoded internal ZCF name and file size, then restarts the process so startup ingests the new Circuit ID -> output mapping for the configured CZone ID.

Request body: multipart form data with a `config_file` file field.

### `GET /api/logs`

Returns recent in-memory log entries.

## Troubleshooting

### SocketCAN open failure

- Confirm the interface exists: `ip link show awlink0`.
- Confirm the process can configure the link if `CAN_AUTO_UP=1`.
- If the link is managed elsewhere, set `CAN_AUTO_UP=0` and bring it up manually.

Manual setup example:

```bash
sudo ip link set awlink0 down
sudo ip link set awlink0 type can bitrate 250000
sudo ip link set awlink0 up
```

### ENOBUFS during CAN send

The emulator retries ENOBUFS automatically. Persistent failures usually indicate bus load, missing ACKs, interface driver issues, or wiring/termination problems.

### Serial permission denied

Check the serial device path and permissions. On many distributions the user must be in the `dialout` group, or the device may need a udev rule.

### No web page reachable

- Confirm the process is still running.
- Confirm `WEB_HOST` and `WEB_PORT`.
- If connecting remotely, bind to `0.0.0.0` and ensure local firewall rules allow the port.

## Project files

- `czone_emulator.py` — headless Linux application with SocketCAN, CZone protocol, Flask web UI/API, and Modbus bridge.
- `CzRaymarineMFDSwitches.ino` — related firmware/example artifact.

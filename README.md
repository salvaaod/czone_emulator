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

### Web settings

- `WEB_HOST`: Flask bind host. Default: `0.0.0.0`.
- `WEB_PORT`: Flask bind port. Default: `8080`.

## Web UI and HTTP API

### `GET /`

Returns the lightweight monitoring/control web page.

### `GET /api/state`

Returns switch states, DIP switch value, adjustable output currents, and keyboard mappings.

### `POST /api/toggle`

Toggles a switch and returns the updated state.

Request body:

```json
{ "switch_id": 1 }
```

Valid `switch_id` values are `1` through `4`.

### `POST /api/output_current`

Sets an adjustable output current. Values are quantized to 0.1 A and clamped to the supported single-byte range.

Request body:

```json
{ "output_index": 1, "amps": 3.5 }
```

Valid `output_index` values are `1` through `4`.

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

# CZone Emulator

A Python-based emulator for a CZone-style switch interface over NMEA 2000/CAN, with Modbus RTU bridging for physical breaker devices.

It supports:
- **GUI mode** (Tkinter) for desktop use.
- **Headless mode** (Flask web UI/API), automatically enabled on Linux when `DISPLAY` is not set.
- **Two CAN backends**:
  - **GCAN DLL** (`ECanVci.dll`) for Windows-like USB-CAN adapters.
  - **SocketCAN** (`python-can`) for Linux.

## What the emulator does

At runtime, the app:
- Selects CAN transport from environment variables and OS defaults.
- Opens serial Modbus RTU to poll/write breaker state (switch IDs 1..4).
- Publishes NMEA 2000 identity/status frames periodically.
- Maintains virtual switch state and output current values.
- In headless mode, exposes a web UI and JSON API.

## Installation

## 1) Clone and create a virtual environment

```bash
git clone <your-repo-url>
cd czone_emulator
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
# .venv\Scripts\activate   # Windows PowerShell
```

## 2) Install Python dependencies

```bash
pip install flask pyserial python-can
```

Notes:
- `python-can` is required when using the **SocketCAN** backend.
- Tkinter is used for GUI mode. On some Linux distros you may need to install system Tk packages.

## 3) Platform prerequisites

### Windows (GCAN)
- Ensure `ECanVci.dll` is present (default: same directory as `czone_emulator.py`), or set `GCAN_DLL_PATH`.
- Ensure your CAN adapter/driver is installed.

### Linux (SocketCAN)
- Ensure SocketCAN tools exist (`iproute2`) and your interface is available (default `awlink0`).
- The app can auto-bring the interface up unless disabled.
- Ensure serial-device permissions for your RS485 port (e.g. `/dev/ttyAS3`).

## Usage

Run:

```bash
python czone_emulator.py
```

### Runtime mode selection

- `HEADLESS=1` forces headless mode.
- On Linux with no `DISPLAY`, headless mode is selected automatically.
- Otherwise GUI mode is used.

### Default behavior by OS

- **Windows default**: `CAN_BACKEND=gcan`, serial `SERIAL_PORT=COM8`, `SERIAL_BAUDRATE=115200`.
- **Linux default**: `CAN_BACKEND=socketcan`, CAN interface `awlink0`, serial COM alias `COM8 -> /dev/ttyAS3`.

## Common startup examples

### Windows example

```bat
set CAN_BACKEND=gcan
set SERIAL_PORT=COM8
set SERIAL_BAUDRATE=115200
python czone_emulator.py
```

### Linux GUI example (with DISPLAY)

```bash
export CAN_BACKEND=socketcan
export CAN_CHANNEL=awlink0
export SERIAL_PORT=/dev/ttyAS3
export SERIAL_BAUDRATE=115200
python czone_emulator.py
```

### Linux headless + web UI example

```bash
export HEADLESS=1
export WEB_HOST=0.0.0.0
export WEB_PORT=8080
export CAN_BACKEND=socketcan
export CAN_CHANNEL=awlink0
export SERIAL_PORT=/dev/ttyAS3
python czone_emulator.py
```

Then open: `http://<host>:8080/`

## Configuration reference

## CAN settings

- `CAN_BACKEND`: `gcan` or `socketcan`.
- `CAN_CHANNEL`: SocketCAN interface name (default `awlink0`).
- `GCAN_DLL_PATH`: path to GCAN DLL.
- `CAN_AUTO_UP`: Linux SocketCAN auto-link setup (`1` default; falsey: `0`, `false`, `no`).
- `CAN_BITRATE`: bitrate used when auto-configuring link (default `250000`).
- `CAN_SEND_TIMEOUT_SECONDS`: send timeout for SocketCAN (default `0.2`).
- `CAN_SEND_RETRY_DELAY_SECONDS`: retry delay on ENOBUFS (default `0.05`).
- `CAN_SEND_MAX_RETRIES`: max ENOBUFS retries (default `40`).

## Serial/Modbus settings

- `SERIAL_PORT`: configured serial port (default `COM8`).
- `SERIAL_BAUDRATE`: serial baudrate (default `115200`).
- `SERIAL_LINUX_DEFAULT_PORT`: Linux default for COM alias fallback (default `/dev/ttyAS3`).
- `SERIAL_COM_ALIAS_MAP`: Linux COM-to-device mapping list, e.g.:
  - `COM8=/dev/ttyAS3,COM9=/dev/ttyUSB0`

## UI/Web settings

- `HEADLESS`: `1/true/yes` to force headless mode.
- `WEB_HOST`: Flask bind host in headless mode (default `0.0.0.0`).
- `WEB_PORT`: Flask bind port in headless mode (default `8080`).

## Headless HTTP API

When in headless mode:

- `GET /api/state`
  - Returns switch states, DIP switch value, adjustable output currents, and keyboard mappings.
- `POST /api/toggle`
  - JSON: `{ "switch_id": 1..4 }`
  - Toggles a switch and returns updated state.
- `POST /api/output_current`
  - JSON: `{ "output_index": 1..4, "amps": <float> }`
  - Sets output current (quantized to 0.1 A, clamped to supported range).
- `GET /api/logs`
  - Returns recent log entries.

## Troubleshooting

- **DLL not found / GCAN startup failure**
  - Verify `ECanVci.dll` location or set `GCAN_DLL_PATH`.
- **SocketCAN open failure**
  - Verify interface exists, permissions, and bitrate.
  - If needed, set `CAN_AUTO_UP=1` and check `CAN_BITRATE`.
- **ENOBUFS on Linux CAN send**
  - App retries automatically; persistent issues usually indicate bus/ack/load problems.
- **No GUI appears on Linux**
  - If `DISPLAY` is unset, app intentionally runs headless.
- **Serial permission denied**
  - Add user to the proper group (often `dialout`) or adjust udev/device permissions.

## Project files

- `czone_emulator.py` — main application (CAN, CZone protocol, GUI, headless web UI/API, Modbus bridge).
- `ECanVci.dll` — GCAN DLL used by the Windows backend.
- `CzRaymarineMFDSwitches.ino` — related firmware/example artifact.

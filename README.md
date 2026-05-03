# CZone Emulator

This repository provides a cross-platform CZone switch emulator for lab/bench testing NMEA 2000 integrations without dedicated CZone hardware.

## Runtime architecture

`czone_emulator.py` now selects CAN and serial transport at startup while keeping the same CZone protocol and Tkinter behavior.

- **Windows default:** GCAN backend (`ECanVci.dll`) + COM-style RS485 port.
- **Linux default:** SocketCAN backend on `awlink0` + RS485 `/dev/ttyAS3` at `115200`.

## CAN backend selection

Selection order:
1. `CAN_BACKEND` override (`gcan` or `socketcan`)
2. OS detection (`platform.system()`)

Channel/interface selection:
- `CAN_CHANNEL` override
- Linux default: `awlink0`
- Linux auto-bring-up controls:
  - `CAN_AUTO_UP` (default `1`) attempts `ip link set <iface> up type can bitrate <CAN_BITRATE>` when link is DOWN
  - `CAN_BITRATE` (default `250000`)
  - `CAN_SEND_TIMEOUT_SECONDS` (default `0.2`)
  - `CAN_SEND_RETRY_DELAY_SECONDS` (default `0.05`) retries ENOBUFS (`No buffer space available`) send errors

GCAN DLL path:
- `GCAN_DLL_PATH` override
- default: `ECanVci.dll` beside `czone_emulator.py`

Expected startup log example:

```text
Startup CAN selection: os=Linux, backend=socketcan, interface=awlink0, dll=n/a
```

## RS485 serial resolution

Serial selection supports COM compatibility on Linux:

- **Windows:** `COMx` values are used unchanged.
- **Linux:**
  - native `/dev/...` paths are used directly,
  - COM-style values are mapped via aliasing.

Defaults and overrides:
- `SERIAL_PORT` (default `COM8`)
- `SERIAL_BAUDRATE` (default `115200`)
- `SERIAL_LINUX_DEFAULT_PORT` (default `/dev/ttyAS3`)
- `SERIAL_COM_ALIAS_MAP` (comma-separated mapping, example `COM8=/dev/ttyAS3,COM9=/dev/ttyUSB0`)

Expected startup log example:

```text
Startup serial selection: configured=COM8, resolved=/dev/ttyAS3, baudrate=115200
```

## Startup examples

### Windows (GCAN + COM)

```bash
python czone_emulator.py
```

Optional explicit override:

```bash
set CAN_BACKEND=gcan
set SERIAL_PORT=COM8
set SERIAL_BAUDRATE=115200
python czone_emulator.py
```

### Linux (SocketCAN + awlink0 + /dev/ttyAS3)

```bash
export CAN_BACKEND=socketcan
export CAN_CHANNEL=awlink0
export CAN_AUTO_UP=1
export CAN_BITRATE=250000
export SERIAL_PORT=/dev/ttyAS3
export SERIAL_BAUDRATE=115200
python czone_emulator.py
```

Linux COM-alias example:

```bash
export SERIAL_PORT=COM8
export SERIAL_COM_ALIAS_MAP='COM8=/dev/ttyAS3'
python czone_emulator.py
```

## Troubleshooting

- **Missing/down CAN interface (`awlink0`) on Linux**
  - By default the app attempts to bring the interface UP automatically using `CAN_BITRATE` (250000).
  - If auto-up is disabled (`CAN_AUTO_UP=0`), ensure the interface exists and is up manually (for example via `ip link`).
  - Verify process privileges and CAN bitrate setup match your bus.
  - If no CAN peer is acknowledging frames, SocketCAN may raise ENOBUFS; the app now retries with a short backoff.
- **Linux serial permission denied**
  - Confirm user access to `/dev/ttyAS3` (for example group membership such as `dialout`).
- **COM alias mismatch on Linux**
  - If `SERIAL_PORT` is COM-style, verify `SERIAL_COM_ALIAS_MAP` points to the intended `/dev/...` device.
- **Startup failure diagnostics**
  - The emulator now reports OS, backend, CAN interface, serial port, and baudrate in failure messages to speed field debugging.

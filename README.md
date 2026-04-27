# CZone Emulator

This repository contains a **Python-based CZone switch emulator** and an **Arduino reference sketch** used as protocol inspiration for a future physical switch device.

## Repository contents

- `czone_emulator.py`  
  Desktop emulator that connects to a USB-CAN adapter (`ECanVci.dll`) and emulates a two-bank (8 switch) CZone switch device over NMEA 2000/CZone PGNs.

- `CzRaymarineMFDSwitches.ino`  
  **Reference/sample code from another project** (not part of the executable Python emulator). It demonstrates how an Arduino-based device interacts with a Raymarine MFD for CZone switch control. It is kept here as a protocol/behavior baseline for building the future hardware switch device.

- `ECanVci.dll`  
  Driver library used by the Python emulator to access the USB-CAN hardware.

## How the Python emulator works

1. Opens the CAN device using `ECanVci.dll` at 250 kbps.
2. Listens for incoming CZone control/configuration PGNs.
3. Handles switch commands from PGN `65280`.
4. Handles authentication/config handshaking from PGN `65290`.
5. Sends periodic updates:
   - heartbeat PGN `65284` (for bank 1 and bank 2)
   - switch-state compatibility PGN `127501`
6. Sends command acknowledgements on PGN `65283` after switch changes.

## Important update: removed PGN 127501/127502 monitoring

The GUI previously displayed incoming network-monitor lines for PGN `127501` and `127502`. This monitoring was not required for the emulator behavior and has been removed.

Current GUI log focus:
- switch command events (ON/OFF)
- runtime/heartbeat status information

## Why `CzRaymarineMFDSwitches.ino` is included

Even though it is from a different project, this file is intentionally included because it documents practical message flow and field usage for:

- `65280` switch command parsing
- `65283` switch change acknowledgement behavior
- `65284` periodic heartbeat behavior
- `65290` configuration/auth flow
- optional compatibility PGNs (`127501`, `127502`) and CZone broadcast behavior (`130817`)

This makes it a useful implementation reference while developing an actual Arduino-based CZone switch module.

## Running the emulator

From this folder:

```bash
python czone_emulator.py
```

Requirements:
- Windows-compatible environment for `ctypes.WinDLL`
- `ECanVci.dll` present in the same directory
- Supported USB-CAN hardware

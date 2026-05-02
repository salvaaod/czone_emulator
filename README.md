# CZone Emulator

This repository provides a **Windows desktop CZone switch emulator** for lab/bench testing NMEA 2000 integrations without dedicated CZone hardware.

## Intended usage

Use this project when you want to:
- validate MFD/keypad behavior against a simulated CZone output interface,
- test message sequencing (stage/commit switch commands) on a live CAN bus,
- verify discovery/identity traffic (address claim + product information),
- manually force switch states and output-current bytes while observing downstream device behavior.

This is a **development/diagnostic emulator**, not a certified marine controller.

## Repository contents

- `czone_emulator.py`  
  Main Python app (CAN transport + CZone protocol handling + Tkinter GUI).

- `ECanVci.dll`  
  Required USB-CAN driver library loaded via `ctypes.WinDLL`.

- `CzRaymarineMFDSwitches.ino`  
  Reference Arduino sketch kept as protocol inspiration for eventual hardware implementation.

## Code behavior (runtime)

At startup the emulator:
1. Opens the USB-CAN adapter at **250 kbps**.
2. Claims a NMEA 2000 address (PGN **60928**) and sends product info (PGN **126996**).
3. Sends immediate heartbeat/status bursts so reconnecting displays can resync quickly.

While running, it:
- listens for PGN **65280** command frames and applies a **stage/commit** model:
  - `0xF1` / `0xF2` stage desired ON/OFF,
  - `0x40` / `0x42` commits the staged state for that key.
- listens for PGN **65290** configuration/authentication frames and marks the emulator authenticated,
- listens for PGN **59904** requests and responds to:
  - PGN 60928 requests with address claim,
  - PGN 126996 requests with product information.

It transmits periodic updates:
- PGN **65284** heartbeat,
- PGN **130817** detailed status (fast-packet) with six output blocks,
- recurring identity traffic (60928 + 126996).

## Switch and keyboard mapping

The emulator tracks **4 logical switches** (S1..S4) mapped from keypad command bytes by sender CZone ID:

- Keyboard ID `2`: `0x05->S1`, `0x06->S2`, `0x07->S3`, `0x08->S4`
- Keyboard ID `192`: `0x09->S1`, `0x0A->S2`, `0x0B->S3`, `0x0C->S4`

Unmapped keys are ignored and logged.

## Output current behavior

- 6 outputs are encoded into PGN 130817 status blocks.
- Outputs **1..4** are user-adjustable in the GUI (0.0 to 25.5 A in 0.1 A steps).
- Outputs **5..6** are reserved and always reported as `0.0 A`.

## GUI behavior

The Tkinter UI provides:
- live switch state display (S1..S4),
- manual switch toggles,
- manual current setpoints for outputs 1..4,
- console logging of RX/TX protocol events.

Background loop timing:
- CAN polling every ~50 ms,
- heartbeat every ~2 s,
- detailed status every ~2 s,
- identity re-announce every ~60 s.

## Running the emulator

From this directory:

```bash
python czone_emulator.py
```

Requirements:
- Windows-compatible Python runtime (`ctypes.WinDLL` support),
- `ECanVci.dll` present beside `czone_emulator.py`,
- supported USB-CAN hardware connected,
- bus configured for 250 kbps.

## Notes

- The `.ino` file is documentation/reference material and is **not** executed by the Python app.
- If CAN traffic is present but switches do not change, verify sender CZone ID and key mapping first.

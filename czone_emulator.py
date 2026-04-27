import ctypes
import os
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------- CONFIG ----------------

USBCAN_II = 4
DEVICE_INDEX = 0
CAN_INDEX = 0

TIMING0_250K = 0x01
TIMING1_250K = 0x1C

SRC = 1

PGN_65280 = 65280
PGN_65283 = 65283
PGN_65284 = 65284
PGN_65290 = 65290
PGN_127501 = 127501

CZONE_MESSAGE = 0x9927
CZONE_DIP_SWITCH = 200

BANK1 = 0x1D
BANK2 = 0x1B

# ---------------- CAN STRUCTS ----------------


class CAN_OBJ(ctypes.Structure):
    _fields_ = [
        ("ID", ctypes.c_uint),
        ("TimeStamp", ctypes.c_uint),
        ("TimeFlag", ctypes.c_ubyte),
        ("SendType", ctypes.c_ubyte),
        ("RemoteFlag", ctypes.c_ubyte),
        ("ExternFlag", ctypes.c_ubyte),
        ("DataLen", ctypes.c_ubyte),
        ("Data", ctypes.c_ubyte * 8),
        ("Reserved", ctypes.c_ubyte * 3),
    ]


class INIT_CONFIG(ctypes.Structure):
    _fields_ = [
        ("AccCode", ctypes.c_uint),
        ("AccMask", ctypes.c_uint),
        ("Reserved", ctypes.c_uint),
        ("Filter", ctypes.c_ubyte),
        ("Timing0", ctypes.c_ubyte),
        ("Timing1", ctypes.c_ubyte),
        ("Mode", ctypes.c_ubyte),
    ]


# ---------------- GCAN DRIVER ----------------


class GCAN:
    def __init__(self, dll_path: str):
        dll_path = os.path.abspath(dll_path)

        if not os.path.exists(dll_path):
            raise FileNotFoundError(f"DLL not found: {dll_path}")

        print(f"Loading DLL from: {dll_path}")

        self.dll = ctypes.WinDLL(dll_path)

        self.dll.OpenDevice.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
        self.dll.InitCAN.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(INIT_CONFIG)]
        self.dll.StartCAN.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]

        self.dll.Transmit.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(CAN_OBJ),
            ctypes.c_ulong,
        ]

        self.dll.Receive.argtypes = [
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.POINTER(CAN_OBJ),
            ctypes.c_ulong,
            ctypes.c_int,
        ]

    def open(self):
        if self.dll.OpenDevice(USBCAN_II, DEVICE_INDEX, 0) == 0:
            raise RuntimeError("OpenDevice failed")

        cfg = INIT_CONFIG(
            AccCode=0,
            AccMask=0xFFFFFFFF,
            Reserved=0,
            Filter=0,
            Timing0=TIMING0_250K,
            Timing1=TIMING1_250K,
            Mode=0,
        )

        if self.dll.InitCAN(USBCAN_II, DEVICE_INDEX, CAN_INDEX, ctypes.byref(cfg)) == 0:
            raise RuntimeError("InitCAN failed")

        if self.dll.StartCAN(USBCAN_II, DEVICE_INDEX, CAN_INDEX) == 0:
            raise RuntimeError("StartCAN failed")

        print("GCAN device opened successfully")

    def send(self, can_id, data: bytes):
        obj = CAN_OBJ()
        obj.ID = can_id
        obj.ExternFlag = 1
        obj.DataLen = len(data)

        for i, b in enumerate(data):
            obj.Data[i] = b

        self.dll.Transmit(USBCAN_II, DEVICE_INDEX, CAN_INDEX, ctypes.byref(obj), 1)

    def recv(self):
        buffer = (CAN_OBJ * 50)()
        count = self.dll.Receive(USBCAN_II, DEVICE_INDEX, CAN_INDEX, buffer, 50, 0)
        return buffer[:count]


# ---------------- NMEA2000 HELPERS ----------------


def n2k_id(priority, pgn, src, dst=255):
    pf = (pgn >> 8) & 0xFF
    if pf < 240:
        return (priority << 26) | (pgn << 8) | (dst << 8) | src
    return (priority << 26) | (pgn << 8) | src


def parse_pgn(can_id):
    pf = (can_id >> 16) & 0xFF
    ps = (can_id >> 8) & 0xFF
    if pf < 240:
        return pf << 8
    return (pf << 8) | ps


def u16(v):
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


# ---------------- CZONE DEVICE ----------------


@dataclass
class CZone:
    dev: GCAN
    state1: int = 0
    state2: int = 0
    authenticated: bool = False
    on_switch_event: Optional[Callable[[int, bool], None]] = None

    def send(self, pgn, data):
        self.dev.send(n2k_id(7, pgn, SRC), data)

    def get_switch_states(self):
        states = []
        for switch_id in range(1, 9):
            if switch_id <= 4:
                mask = 1 << (switch_id - 1)
                states.append(bool(self.state1 & mask))
            else:
                mask = 1 << (switch_id - 5)
                states.append(bool(self.state2 & mask))
        return states

    def heartbeat(self, bank):
        if self.authenticated:
            state = self.state1 if bank == BANK1 else self.state2
            data = u16(CZONE_MESSAGE) + bytes([bank, 0x0F, state]) + u16(0) + bytes([0])
        else:
            data = u16(CZONE_MESSAGE) + bytes([0xFF]) + u16(0x0F0F) + u16(0) + bytes([0])

        self.send(PGN_65284, data)

    def status(self):
        status = 0

        # 127501 N2K Binary Status encoding. Matches the .ino mapping style.
        for i in range(4):
            if self.state1 & (1 << i):
                status |= 1 << (2 * i)

            if self.state2 & (1 << i):
                status |= 1 << (2 * (i + 4))

        payload = bytes([0]) + status.to_bytes(7, "little")
        self.send(PGN_127501, payload)

    def ack(self, bank):
        data = u16(CZONE_MESSAGE) + bytes([bank, 0]) + u16(0) + bytes([0, 0x10])
        self.send(PGN_65283, data)

    def _toggle_switch(self, switch_code: int) -> bool:
        if switch_code <= 0x08:
            bit = switch_code - 0x05
            self.state1 ^= 1 << bit
            return bool(self.state1 & (1 << bit))

        bit = switch_code - 0x09
        self.state2 ^= 1 << bit
        return bool(self.state2 & (1 << bit))

    def handle_command(self, data):
        if len(data) < 7:
            return

        if int.from_bytes(data[:2], "little") != CZONE_MESSAGE:
            return

        if data[5] != CZONE_DIP_SWITCH:
            return

        sw = data[2]
        cmd = data[6]

        if not (0x05 <= sw <= 0x0C):
            return

        if cmd in (0xF1, 0xF2):
            is_on = self._toggle_switch(sw)
            state_text = "ON" if is_on else "OFF"
            message = f"Switch {sw} -> {state_text}"
            print(message)
            if self.on_switch_event:
                self.on_switch_event(sw, is_on)

            self.status()
            self.ack(BANK1 if sw <= 0x08 else BANK2)
        elif cmd == 0x40:
            # End-of-change sync command from MFD.
            self.ack(BANK1 if sw <= 0x08 else BANK2)

    def handle_config(self):
        print("CZone authenticated")
        self.authenticated = True

    def process_rx(self):
        frames = self.dev.recv()

        for f in frames:
            data = bytes(f.Data[:f.DataLen])
            pgn = parse_pgn(f.ID)

            if pgn == PGN_65280:
                self.handle_command(data)
            elif pgn == PGN_65290:
                self.handle_config()

    def periodic(self):
        self.heartbeat(BANK1)
        self.heartbeat(BANK2)
        self.status()


class CZoneGui:
    def __init__(self, czone: CZone):
        self.czone = czone
        self.root = tk.Tk()
        self.root.title("CZone Emulator")
        self.root.geometry("560x420")

        tk.Label(self.root, text="Received CZone Switch Commands", font=("Arial", 12, "bold")).pack(
            pady=(10, 4)
        )

        self.switches_label = tk.Label(self.root, text="Switch states: S1:OFF S2:OFF S3:OFF S4:OFF S5:OFF S6:OFF S7:OFF S8:OFF")
        self.switches_label.pack(pady=(0, 8))

        self.log = tk.Text(self.root, wrap="word", height=16, width=72, state="disabled")
        self.log.pack(padx=10, pady=6, fill="both", expand=True)

        self.status_label = tk.Label(self.root, text="Waiting for CAN messages...")
        self.status_label.pack(pady=(0, 10))

        self.czone.on_switch_event = self.record_switch_event
        self.last_periodic = time.time()

    def append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log.configure(state="normal")
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def refresh_switch_states(self):
        states = self.czone.get_switch_states()
        display = " ".join(f"S{i + 1}:{'ON' if state else 'OFF'}" for i, state in enumerate(states))
        self.switches_label.configure(text=f"Switch states: {display}")

    def record_switch_event(self, switch_code: int, is_on: bool):
        switch_id = (switch_code - 0x05) + 1
        state_text = "ON" if is_on else "OFF"
        self.append_log(f"Switch {switch_id} (code 0x{switch_code:02X}) -> {state_text}")
        self.refresh_switch_states()

    def poll_can(self):
        self.czone.process_rx()

        if time.time() - self.last_periodic > 2:
            self.last_periodic = time.time()
            self.czone.periodic()
            self.status_label.configure(text="Heartbeat/status sent")
            self.refresh_switch_states()

        self.root.after(50, self.poll_can)

    def run(self):
        print("CZone emulator GUI running...")
        self.append_log("CZone emulator GUI running...")
        self.refresh_switch_states()
        self.poll_can()
        self.root.mainloop()


# ---------------- MAIN ----------------


def main():
    runtime_dir = os.path.dirname(os.path.abspath(__file__))
    dll_path = os.path.join(runtime_dir, "ECanVci.dll")

    dev = GCAN(dll_path)
    dev.open()

    czone = CZone(dev)
    gui = CZoneGui(czone)
    gui.run()


if __name__ == "__main__":
    main()

import ctypes
import os
import time
from dataclasses import dataclass

# ---------------- CONFIG ----------------

USBCAN_II = 4
DEVICE_INDEX = 0
CAN_INDEX = 0

TIMING0_250K = 0x01
TIMING1_250K = 0x1C

SRC = 2

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

    def send(self, pgn, data):
        self.dev.send(n2k_id(7, pgn, SRC), data)

    def heartbeat(self, bank):
        if self.authenticated:
            state = self.state1 if bank == BANK1 else self.state2
            data = u16(CZONE_MESSAGE) + bytes([bank, 0x0F, state]) + u16(0) + bytes([0])
        else:
            data = u16(CZONE_MESSAGE) + bytes([0xFF]) + u16(0x0F0F) + u16(0) + bytes([0])

        self.send(PGN_65284, data)

    def status(self):
        status = 0

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

        idx = sw - 5

        if sw <= 0x08:
            if cmd == 0xF1:
                self.state1 |= (1 << idx)
            elif cmd == 0xF2:
                self.state1 &= ~(1 << idx)
        else:
            if cmd == 0xF1:
                self.state2 |= (1 << (idx - 4))
            elif cmd == 0xF2:
                self.state2 &= ~(1 << (idx - 4))

        print(f"Switch {sw} -> {'ON' if cmd == 0xF1 else 'OFF'}")

        self.status()
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

# ---------------- MAIN ----------------

def main():
    dll_path = r"C:\Users\DELL LAPTOP\Desktop\czone_emulator\ECanVci.dll"

    dev = GCAN(dll_path)
    dev.open()

    czone = CZone(dev)

    last = time.time()

    print("CZone emulator running...")

    while True:
        czone.process_rx()

        if time.time() - last > 2:
            last = time.time()
            czone.periodic()


if __name__ == "__main__":
    main()


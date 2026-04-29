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

SRC = 0

PGN_60928 = 60928
PGN_59904 = 59904
PGN_65280 = 65280
PGN_65283 = 65283
PGN_65284 = 65284
PGN_65290 = 65290
PGN_126996 = 126996
PGN_130817 = 130817

CZONE_MESSAGE = 0x9927
CZONE_DIP_SWITCH_DEFAULT = 1

N2K_UNIQUE_NUMBER = 197135
N2K_MANUFACTURER_CODE = 295
N2K_DEVICE_INSTANCE_LOWER = 2
N2K_DEVICE_INSTANCE_UPPER = 0
N2K_DEVICE_FUNCTION = 140
N2K_DEVICE_CLASS = 30
N2K_SYSTEM_INSTANCE = 0
N2K_INDUSTRY_GROUP = 4

N2K_DB_VERSION = 2000
N2K_CERTIFICATION_LEVEL = 0
N2K_LOAD_EQUIVALENCY = 0
N2K_MANUFACTURER_PRODUCT_CODE = 18830
N2K_MODEL_ID = "01 (80-911-0010-00)"
N2K_SOFTWARE_ID = "6.26.24.0"
N2K_HARDWARE_ID = "A"
N2K_SERIAL_ID = "J4616585-0068"

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


def parse_src(can_id):
    return can_id & 0xFF


def u16(v):
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


def n2k_string_field(text: str, field_len: int = 32) -> bytes:
    raw = text.encode("ascii", errors="ignore")[: field_len - 1]
    return raw + b"\x00" + (b"\xFF" * (field_len - len(raw) - 1))


def encode_iso_name() -> bytes:
    value = 0
    value |= N2K_UNIQUE_NUMBER & 0x1FFFFF
    value |= (N2K_MANUFACTURER_CODE & 0x7FF) << 21
    value |= (N2K_DEVICE_INSTANCE_LOWER & 0x07) << 32
    value |= (N2K_DEVICE_INSTANCE_UPPER & 0x1F) << 35
    value |= (N2K_DEVICE_FUNCTION & 0xFF) << 40
    value |= 0 << 48  # Reserved
    value |= (N2K_DEVICE_CLASS & 0x7F) << 49
    value |= (N2K_SYSTEM_INSTANCE & 0x0F) << 56
    value |= (N2K_INDUSTRY_GROUP & 0x07) << 60
    value |= 1 << 63  # Reserved bit
    return value.to_bytes(8, "little")


# ---------------- CZONE DEVICE ----------------


@dataclass
class CZone:
    dev: GCAN
    state1: int = 0
    state2: int = 0
    authenticated: bool = False
    on_switch_event: Optional[Callable[[int, bool], None]] = None
    on_log_event: Optional[Callable[[str], None]] = None
    mfd_sync_state1: int = 0
    mfd_sync_state2: int = 0
    dip_switch: int = CZONE_DIP_SWITCH_DEFAULT
    pending_commands: dict[int, int] | None = None

    def __post_init__(self):
        if self.pending_commands is None:
            self.pending_commands = {}

    def send(self, pgn, data, priority=7):
        self.dev.send(n2k_id(priority, pgn, SRC), data)

    def send_fast_packet(self, pgn: int, payload: bytes, priority: int = 6):
        seq = int(time.time() * 1000) & 0x07
        frame_index = 0
        offset = 0
        first = bytes([(seq << 5) | frame_index, len(payload)]) + payload[:6]
        self.send(pgn, first, priority=priority)
        frame_index += 1
        offset = 6

        while offset < len(payload):
            chunk = payload[offset : offset + 7]
            frame = bytes([(seq << 5) | frame_index]) + chunk
            self.send(pgn, frame, priority=priority)
            frame_index += 1
            offset += 7

    def _log(self, message: str):
        print(message)
        if self.on_log_event:
            self.on_log_event(message)

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

    def detailed_status(self):
        # Minimal proprietary 130817 payload with both bank states.
        payload = (
            u16(CZONE_MESSAGE)
            + bytes([BANK1, self.state1, BANK2, self.state2, self.mfd_sync_state1, self.mfd_sync_state2, 0x00])
        )
        self.send_fast_packet(PGN_130817, payload, priority=7)
        self._log(
            f"TX 130817 detailed: bank1=0x{self.state1:02X} bank2=0x{self.state2:02X}"
        )

    def address_claim(self):
        self.send(PGN_60928, encode_iso_name(), priority=6)
        self._log("TX 60928 ISO address claim")

    def product_information(self):
        payload = (
            u16(N2K_DB_VERSION)
            + u16(N2K_MANUFACTURER_PRODUCT_CODE)
            + n2k_string_field(N2K_MODEL_ID)
            + n2k_string_field(N2K_SOFTWARE_ID)
            + n2k_string_field(N2K_HARDWARE_ID)
            + n2k_string_field(N2K_SERIAL_ID)
            + bytes([N2K_CERTIFICATION_LEVEL & 0xFF, N2K_LOAD_EQUIVALENCY & 0xFF])
        )
        self.send_fast_packet(PGN_126996, payload, priority=6)
        self._log("TX 126996 product information")

    def ack(self, bank):
        sync_state = self.mfd_sync_state1 if bank == BANK1 else self.mfd_sync_state2
        data = u16(CZONE_MESSAGE) + bytes([bank, sync_state]) + u16(0) + bytes([0, 0x10])
        self.send(PGN_65283, data)
        self._log(f"TX 65283 ack: bank=0x{bank:02X} sync=0x{sync_state:02X}")

    @staticmethod
    def _sync_mask_for_switch_code(switch_code: int) -> int:
        # Matches .ino: 0x05->0x01, 0x06->0x04, 0x07->0x10, 0x08->0x40.
        return 1 << (2 * ((switch_code - 0x05) % 4))

    def _set_switch(self, switch_code: int, is_on: bool) -> bool:
        if switch_code <= 0x08:
            bit = switch_code - 0x05
            mask = 1 << bit
            prev = bool(self.state1 & mask)
            self.state1 = (self.state1 | mask) if is_on else (self.state1 & ~mask)
            if prev != is_on:
                self.mfd_sync_state1 ^= self._sync_mask_for_switch_code(switch_code)
            return bool(self.state1 & mask)

        bit = switch_code - 0x09
        mask = 1 << bit
        prev = bool(self.state2 & mask)
        self.state2 = (self.state2 | mask) if is_on else (self.state2 & ~mask)
        if prev != is_on:
            self.mfd_sync_state2 ^= self._sync_mask_for_switch_code(switch_code)
        return bool(self.state2 & mask)

    def handle_command(self, data):
        self._log(f"RX 65280 raw: {data.hex(' ')}")

        if len(data) < 7:
            self._log("RX 65280 ignored: frame shorter than 7 bytes")
            return

        if int.from_bytes(data[:2], "little") != CZONE_MESSAGE:
            self._log("RX 65280 ignored: signature is not CZone message")
            return

        if data[5] != self.dip_switch:
            if self.authenticated:
                self._log(
                    f"RX 65280 DIP auto-adjust: got {data[5]}, expected {self.dip_switch}; switching to received DIP"
                )
                self.dip_switch = data[5]
            else:
                self._log(
                    f"RX 65280 ignored: DIP mismatch, got {data[5]}, expected {self.dip_switch}"
                )
                return

        sw = data[2]
        cmd = data[6]

        if not (0x05 <= sw <= 0x0C):
            self._log(f"RX 65280 ignored: unsupported switch code 0x{sw:02X}")
            return

        if cmd in (0xF1, 0xF2):
            # Stage command and apply on commit (0x40) to match CZone sequencing.
            self.pending_commands[sw] = cmd
            desired = cmd == 0xF1
            self._log(f"RX 65280 staged: switch=0x{sw:02X} desired={'ON' if desired else 'OFF'}")
        elif cmd in (0x40, 0x42):
            staged = self.pending_commands.get(sw)
            if staged in (0xF1, 0xF2):
                is_on = self._set_switch(sw, staged == 0xF1)
                self.pending_commands.pop(sw, None)
            else:
                # No staged value for this switch: keep existing state.
                is_on = bool((self.state1 if sw <= 0x08 else self.state2) & (1 << ((sw - 0x05) % 4)))
            state_text = "ON" if is_on else "OFF"
            switch_id = (sw - 0x05) + 1
            message = f"Switch {switch_id} -> {state_text}"
            self._log(message)
            if self.on_switch_event:
                self.on_switch_event(sw, is_on)
            self.ack(BANK1 if sw <= 0x08 else BANK2)
            self.heartbeat(BANK1 if sw <= 0x08 else BANK2)
            self.detailed_status()
        else:
            self._log(f"RX 65280 ignored: unsupported command 0x{cmd:02X}")

    def handle_config(self, data):
        if len(data) < 8:
            self._log("RX 65290 ignored: frame shorter than 8 bytes")
            return
        if int.from_bytes(data[:2], "little") != CZONE_MESSAGE:
            self._log("RX 65290 ignored: signature is not CZone message")
            return
        if data[7] != self.dip_switch:
            self._log(
                f"RX 65290 DIP auto-adjust: got {data[7]}, expected {self.dip_switch}; switching to received DIP"
            )
            self.dip_switch = data[7]
        self._log("CZone authenticated")
        self.authenticated = True

    def handle_request(self, src: int, data: bytes):
        if len(data) < 3:
            return
        requested_pgn = data[0] | (data[1] << 8) | (data[2] << 16)
        if requested_pgn == PGN_60928:
            self._log(f"RX 59904 request from {src}: PGN 60928")
            self.address_claim()
        elif requested_pgn == PGN_126996:
            self._log(f"RX 59904 request from {src}: PGN 126996")
            self.product_information()

    def process_rx(self):
        frames = self.dev.recv()

        for f in frames:
            data = bytes(f.Data[:f.DataLen])
            pgn = parse_pgn(f.ID)
            src = parse_src(f.ID)

            if pgn == PGN_65280:
                self.handle_command(data)
            elif pgn == PGN_65290:
                self.handle_config(data)
            elif pgn == PGN_59904:
                self.handle_request(src, data)

    def periodic(self):
        self.address_claim()
        self.product_information()
        self.heartbeat(BANK1)
        self.heartbeat(BANK2)
        self.detailed_status()
        if self.authenticated:
            self.ack(BANK1)
            self.ack(BANK2)


class CZoneGui:
    def __init__(self, czone: CZone):
        self.czone = czone
        self.root = tk.Tk()
        self.root.title("CZone Emulator")
        self.root.geometry("560x420")

        tk.Label(self.root, text="Received CZone Switch Commands", font=("Arial", 12, "bold")).pack(
            pady=(10, 4)
        )
        dip_frame = tk.Frame(self.root)
        dip_frame.pack(pady=(0, 6))
        tk.Label(dip_frame, text="CZone DIP:").pack(side="left")
        self.dip_var = tk.StringVar(value=str(self.czone.dip_switch))
        self.dip_entry = tk.Entry(dip_frame, textvariable=self.dip_var, width=6)
        self.dip_entry.pack(side="left", padx=(6, 6))
        self.dip_entry.bind("<Return>", lambda _: self.apply_dip())
        tk.Button(dip_frame, text="Apply", command=self.apply_dip).pack(side="left")

        self.switches_label = tk.Label(self.root, text="Switch states: S1:OFF S2:OFF S3:OFF S4:OFF S5:OFF S6:OFF S7:OFF S8:OFF")
        self.switches_label.pack(pady=(0, 8))

        self.log = tk.Text(self.root, wrap="word", height=16, width=72, state="disabled")
        self.log.pack(padx=10, pady=6, fill="both", expand=True)

        self.status_label = tk.Label(self.root, text="Waiting for CAN messages...")
        self.status_label.pack(pady=(0, 10))

        self.czone.on_switch_event = self.record_switch_event
        self.czone.on_log_event = self.append_log
        now = time.time()
        self.last_heartbeat = now
        self.last_ack = now
        self.last_status = now
        self.last_n2k_identity = now - 60

    def append_log(self, message: str):
        timestamp = time.strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        self.log.configure(state="normal")
        self.log.insert(tk.END, line + "\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def apply_dip(self):
        raw = self.dip_var.get().strip()
        try:
            dip_value = int(raw, 0)
        except ValueError:
            self.append_log(f"Invalid DIP value '{raw}'. Keep current {self.czone.dip_switch}.")
            self.dip_var.set(str(self.czone.dip_switch))
            return

        if not (0 <= dip_value <= 255):
            self.append_log(f"Invalid DIP value '{raw}'. Expected 0..255.")
            self.dip_var.set(str(self.czone.dip_switch))
            return

        self.czone.dip_switch = dip_value
        self.append_log(f"CZone DIP updated to {dip_value}.")

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
        now = time.time()

        if now - self.last_heartbeat > 2:
            self.last_heartbeat = now
            self.czone.heartbeat(BANK1)
            self.czone.heartbeat(BANK2)
            self.status_label.configure(text="Heartbeat sent")

        if now - self.last_n2k_identity > 60:
            self.last_n2k_identity = now
            self.czone.address_claim()
            self.czone.product_information()

        if self.czone.authenticated and now - self.last_ack > 0.5:
            self.last_ack = now
            self.czone.ack(BANK1)
            self.czone.ack(BANK2)

        if now - self.last_status > 10:
            self.last_status = now
            self.czone.detailed_status()

        self.refresh_switch_states()

        self.root.after(50, self.poll_can)

    def run(self):
        print("CZone emulator GUI running...")
        self.append_log("CZone emulator GUI running...")
        self.czone.address_claim()
        self.czone.product_information()
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

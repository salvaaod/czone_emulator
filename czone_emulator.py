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

SRC = 20

PGN_60928 = 60928
PGN_59904 = 59904
PGN_65280 = 65280
PGN_65284 = 65284
PGN_65290 = 65290
PGN_126996 = 126996
PGN_130817 = 130817

CZONE_MESSAGE = 0x9927
CZONE_DIP_SWITCH_DEFAULT = 2

N2K_UNIQUE_NUMBER = 123456
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
N2K_MODEL_ID = "0I (80-911-0010-00)"
N2K_SOFTWARE_ID = "1.00"
N2K_HARDWARE_ID = "A"
N2K_SERIAL_ID = "J1234567-89AB"

BANK_ID = 0x02
OUTPUT_COUNT = 6
ADJUSTABLE_OUTPUT_COUNT = 4
CURRENT_STEP_AMPS = 0.1
LOG_TX_130817_DETAILED_CURRENTS = True
KEYBOARD_SWITCH_MAPS = {
    2:   {0x05: 1, 0x06: 2, 0x07: 3, 0x08: 4},
    192: {0x09: 1, 0x0A: 2, 0x0B: 3, 0x0C: 4},
}

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
    state: int = 0
    authenticated: bool = True
    on_switch_event: Optional[Callable[[int, bool], None]] = None
    czone_dip_switch: int = CZONE_DIP_SWITCH_DEFAULT
    pending_commands: dict[int, int] | None = None
    keyboard_switch_maps: dict[int, dict[int, int]] | None = None

    def __post_init__(self):
        self._log("CZone startup: pre-authenticated for immediate display sync")
        self._log(f"Identity: NMEA2000 SRC={SRC}, CZone DIP Switch={self.czone_dip_switch}")
        if self.pending_commands is None:
            self.pending_commands = {}
        if self.keyboard_switch_maps is None:
            self.keyboard_switch_maps = {k: dict(v) for k, v in KEYBOARD_SWITCH_MAPS.items()}
        # Default currents are 0.0 A for all outputs at startup.
        # Outputs 5-6 remain reserved and fixed at 0.0 A.
        self.output_current_tenths = {idx: 0 for idx in range(1, OUTPUT_COUNT + 1)}
        self.output_block_overrides: dict[int, tuple[int, int, int, int]] = {}

    def _normalize_current_tenths(self, value: int) -> int:
        return max(0, min(255, int(value)))

    def set_output_current_tenths(self, output_index: int, value: int):
        if not (1 <= output_index <= OUTPUT_COUNT):
            raise ValueError(f"Output index must be 1..{OUTPUT_COUNT}")
        if output_index > ADJUSTABLE_OUTPUT_COUNT:
            self.output_current_tenths[output_index] = 0
            return
        self.output_current_tenths[output_index] = self._normalize_current_tenths(value)

    def set_output_current(self, output_index: int, amps: float):
        quantized = int(round(float(amps) / CURRENT_STEP_AMPS))
        self.set_output_current_tenths(output_index, quantized)

    def get_output_current_tenths(self, output_index: int) -> int:
        if not (1 <= output_index <= OUTPUT_COUNT):
            raise ValueError(f"Output index must be 1..{OUTPUT_COUNT}")
        if output_index > ADJUSTABLE_OUTPUT_COUNT:
            return 0
        return self.output_current_tenths.get(output_index, 0)

    def get_output_current(self, output_index: int) -> float:
        return self.get_output_current_tenths(output_index) * CURRENT_STEP_AMPS

    def set_output_block_override(self, output_index: int, b0: int, b1: int, b2: int, b3: int):
        if output_index not in (1, 2):
            raise ValueError("Only outputs 1 and 2 support manual low-level block override")
        values = tuple(max(0, min(255, int(v))) for v in (b0, b1, b2, b3))
        self.output_block_overrides[output_index] = values

    def clear_output_block_override(self, output_index: int):
        self.output_block_overrides.pop(output_index, None)

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

    def get_switch_states(self):
        states = []
        for switch_id in range(1, 5):
            mask = 1 << (switch_id - 1)
            states.append(bool(self.state & mask))
        return states

    def heartbeat(self):
        if self.authenticated:
            data = u16(CZONE_MESSAGE) + bytes([BANK_ID, 0x0F, self.state, 0x00, 0x00, 0x00])
        else:
            data = u16(CZONE_MESSAGE) + bytes([0xFF]) + u16(0x0F0F) + u16(0) + bytes([0])

        self.send(PGN_65284, data)

    def detailed_status(self):
        # Legacy PGN 130817 layout: header + six 4-byte output blocks = 28 bytes.
        # Current mapping discovered from bench testing:
        # O1 -> block1 b0, O2 -> block1 b3, O3 -> block2 b2, O4 -> block3 b1,
        # then +3 byte stride for outputs 5 and 6.
        payload = bytearray(u16(CZONE_MESSAGE) + bytes([0x00, BANK_ID]))
        output_bytes = bytearray([0x00, 0x00, 0x04, 0x00] * OUTPUT_COUNT)

        current_byte_positions = {1: 0, 2: 3, 3: 6, 4: 9, 5: 12, 6: 15}
        for output_index, position in current_byte_positions.items():
            current_byte = self.get_output_current_tenths(output_index)
            if output_index > ADJUSTABLE_OUTPUT_COUNT:
                current_byte = 0
            output_bytes[position] = current_byte

        payload.extend(output_bytes)
        self.send_fast_packet(PGN_130817, payload, priority=7)
        if LOG_TX_130817_DETAILED_CURRENTS:
            self._log(
                "TX 130817 detailed currents: "
                + " ".join(f"O{i}={self.get_output_current(i):.1f}A" for i in range(1, ADJUSTABLE_OUTPUT_COUNT + 1))
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

    def _set_switch(self, switch_code: int, is_on: bool) -> bool:
        if not (0x05 <= switch_code <= 0x08):
            return False
        bit = switch_code - 0x05
        mask = 1 << bit
        self.state = (self.state | mask) if is_on else (self.state & ~mask)
        return bool(self.state & mask)

    def _set_output(self, output_index: int, is_on: bool) -> bool:
        return self._set_switch(0x04 + output_index, is_on)

    def handle_command(self, _src: int, data: bytes):
        sender_czone_id = data[5] if len(data) > 5 else None
        sender_text = str(sender_czone_id) if sender_czone_id is not None else "unknown"
        self._log(f"RX 65280 from CZone ID {sender_text} raw: {data.hex(' ')}")

        if len(data) < 7:
            self._log("RX 65280 ignored: frame shorter than 7 bytes")
            return

        if int.from_bytes(data[:2], "little") != CZONE_MESSAGE:
            self._log("RX 65280 ignored: signature is not CZone message")
            return

        if not self.authenticated:
            self.authenticated = True
            self._log("CZone authenticated (implicit via 65280 command)")

        sw = data[2]
        cmd = data[6]
        keyboard_map = self.keyboard_switch_maps.get(sender_czone_id, {})
        output_index = keyboard_map.get(sw)

        if output_index is None:
            self._log(
                f"RX 65280 ignored: unmapped key 0x{sw:02X} from keyboard CZone ID {sender_text}"
            )
            return

        if cmd in (0xF1, 0xF2):
            # Stage command and apply on commit (0x40) to match CZone sequencing.
            self.pending_commands[sw] = cmd
            desired = cmd == 0xF1
            self._log(f"RX 65280 staged: switch=0x{sw:02X} desired={'ON' if desired else 'OFF'}")
        elif cmd in (0x40, 0x42):
            staged = self.pending_commands.get(sw)
            if staged in (0xF1, 0xF2):
                is_on = self._set_output(output_index, staged == 0xF1)
                self.pending_commands.pop(sw, None)
            else:
                is_on = bool(self.state & (1 << (output_index - 1)))
            state_text = "ON" if is_on else "OFF"
            message = f"Output {output_index} <- key 0x{sw:02X} -> {state_text}"
            self._log(message)
            if self.on_switch_event:
                self.on_switch_event(0x04 + output_index, is_on)
            self.heartbeat()
            self.detailed_status()
        else:
            self._log(f"RX 65280 ignored: unsupported command 0x{cmd:02X}")

    def handle_config(self, _src: int, data: bytes):
        sender_czone_id = data[7] if len(data) > 7 else None
        sender_text = str(sender_czone_id) if sender_czone_id is not None else "unknown"
        self._log(f"RX 65290 from CZone ID {sender_text} raw: {data.hex(' ')}")

        if len(data) < 8:
            self._log("RX 65290 ignored: frame shorter than 8 bytes")
            return
        if int.from_bytes(data[:2], "little") != CZONE_MESSAGE:
            self._log("RX 65290 ignored: signature is not CZone message")
            return
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
                self.handle_command(src, data)
            elif pgn == PGN_65290:
                self.handle_config(src, data)
            elif pgn == PGN_59904:
                self.handle_request(src, data)

    def periodic(self):
        self.address_claim()
        self.product_information()
        self.heartbeat()
        self.detailed_status()


class CZoneGui:
    def __init__(self, czone: CZone):
        self.czone = czone
        self.root = tk.Tk()
        self.root.title("CZone Emulator")
        self.root.resizable(False, False)

        tk.Label(self.root, text="CZone OI Emulator", font=("Arial", 12, "bold")).pack(
            pady=(10, 4)
        )
        dip_frame = tk.Frame(self.root)
        dip_frame.pack(pady=(0, 6))
        tk.Label(dip_frame, text=f"CZone DIP Switch: {self.czone.czone_dip_switch}").pack(side="left")
        tk.Label(self.root, text=self._mapping_summary_text(), justify="left", anchor="w").pack(
            fill="x", padx=10, pady=(0, 6)
        )

        self.switches_label = tk.Label(self.root, text="Switch states: S1: OFF    S2: OFF    S3: OFF    S4: OFF")
        self.switches_label.pack(pady=(0, 14))

        manual_frame = tk.Frame(self.root)
        manual_frame.pack(pady=(0, 12))
        tk.Label(manual_frame, text="Manual control:").pack(side="left", padx=(0, 8))
        self.manual_vars = []
        for switch_id in range(1, 5):
            var = tk.BooleanVar(value=False)
            tk.Checkbutton(
                manual_frame,
                text=f"S{switch_id}",
                variable=var,
                command=lambda sid=switch_id, v=var: self.set_switch_from_gui(sid, v.get()),
            ).pack(side="left", padx=(0, 10))
            self.manual_vars.append(var)

        current_frame = tk.LabelFrame(self.root, text="Output currents (A)")
        current_frame.pack(pady=(0, 12), padx=8, fill="x")
        self.current_vars = {}
        for output_index in range(1, ADJUSTABLE_OUTPUT_COUNT + 1):
            row = tk.Frame(current_frame)
            row.pack(fill="x", padx=6, pady=2)
            tk.Label(row, text=f"Output {output_index}", width=10, anchor="w").pack(side="left")
            var = tk.StringVar(value=f"{self.czone.get_output_current(output_index):.1f}")
            spin = tk.Spinbox(
                row,
                from_=0.0,
                to=25.5,
                increment=0.1,
                format="%.1f",
                textvariable=var,
                width=6,
                command=lambda idx=output_index: self.apply_output_current(idx),
            )
            spin.pack(side="left", padx=(0, 6))
            spin.bind("<Return>", lambda _, idx=output_index: self.apply_output_current(idx))
            spin.bind("<FocusOut>", lambda _, idx=output_index: self.apply_output_current(idx))
            self.current_vars[output_index] = var

        self.status_label = tk.Label(self.root, text="Waiting for CAN messages...")
        self.status_label.pack(pady=(0, 10))

        self.czone.on_switch_event = self.record_switch_event
        now = time.time()
        self.last_heartbeat = now
        self.last_status = now
        self.last_n2k_identity = now - 60

    def append_log(self, message: str):
        print(message)

    def refresh_switch_states(self):
        states = self.czone.get_switch_states()
        display = "    ".join(f"S{i + 1}: {'ON' if state else 'OFF'}" for i, state in enumerate(states))
        self.switches_label.configure(text=f"Switch states: {display}")
        for i, state in enumerate(states):
            self.manual_vars[i].set(state)

    def set_switch_from_gui(self, switch_id: int, is_on: bool):
        switch_code = 0x04 + switch_id
        updated = self.czone._set_switch(switch_code, is_on)
        self.append_log(f"Manual switch {switch_id} -> {'ON' if updated else 'OFF'}")
        self.czone.heartbeat()
        self.czone.detailed_status()
        self.refresh_switch_states()

    def apply_output_current(self, output_index: int):
        raw = self.current_vars[output_index].get().strip()
        try:
            amps = float(raw)
        except ValueError:
            self.append_log(f"Invalid current '{raw}' for output {output_index}; keeping previous value.")
            self.current_vars[output_index].set(f"{self.czone.get_output_current(output_index):.1f}")
            return

        self.czone.set_output_current(output_index, amps)
        normalized = self.czone.get_output_current(output_index)
        self.current_vars[output_index].set(f"{normalized:.1f}")
        self.append_log(f"Manual output {output_index} current -> {normalized:.1f} A")
        self.czone.detailed_status()
    def record_switch_event(self, switch_code: int, is_on: bool):
        switch_id = (switch_code - 0x05) + 1
        state_text = "ON" if is_on else "OFF"
        self.append_log(f"Switch {switch_id} (code 0x{switch_code:02X}) -> {state_text}")
        self.refresh_switch_states()

    def _mapping_summary_text(self) -> str:
        segments = []
        for keyboard_id, mapping in sorted(self.czone.keyboard_switch_maps.items()):
            mapped = ", ".join(f"{k:02X}->{v}" for k, v in sorted(mapping.items()))
            segments.append(f"KBD {keyboard_id:03d}: {mapped}")
        return "Mappings:\n" + "\n".join(segments)

    def poll_can(self):
        self.czone.process_rx()
        now = time.time()

        if now - self.last_heartbeat > 2:
            self.last_heartbeat = now
            self.czone.heartbeat()
            self.status_label.configure(text="Heartbeat sent")

        if now - self.last_n2k_identity > 60:
            self.last_n2k_identity = now
            self.czone.address_claim()
            self.czone.product_information()

        if now - self.last_status > 2:
            self.last_status = now
            self.czone.detailed_status()

        self.refresh_switch_states()

        self.root.after(50, self.poll_can)

    def run(self):
        print("CZone emulator GUI running...")
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
    # Push presence/status frames immediately after CAN open so reconnects do not
    # wait for GUI initialization timing.
    for _ in range(3):
        czone.address_claim()
        czone.product_information()
        czone.heartbeat()
        czone.detailed_status()
        time.sleep(0.1)

    gui = CZoneGui(czone)
    gui.run()


if __name__ == "__main__":
    main()

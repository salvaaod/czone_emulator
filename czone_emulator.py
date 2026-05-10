from errno import ENOBUFS
import os
import re
import struct
import subprocess
import threading
import time
from flask import Flask, jsonify, request
from pathlib import Path
from queue import Empty, Queue
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

import serial

# ---------------- CONFIG ----------------

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
# Value 0x0F is OI interface, 0x0A ACOI interface
CZONE_HEARTBEAT_VALUE_DEFAULT = 0x0F

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
N2K_MODEL_ID = "Azimut AC Controller"
N2K_SOFTWARE_ID = "1.00"
N2K_HARDWARE_ID = "A"
N2K_SERIAL_ID = "123456"

OUTPUT_COUNT = 6
ADJUSTABLE_OUTPUT_COUNT = 4
CURRENT_STEP_AMPS = 0.1
LOG_TX_130817_DETAILED_CURRENTS = False
MODBUS_DEFAULT_COM_PORT = "/dev/ttyAS3"
MODBUS_BAUDRATE = 115200
MODBUS_POLL_INTERVAL_SECONDS = 0.25
MODBUS_STATUS_REGISTER = 0x8000
MODBUS_SWITCH_IDS = (1, 2, 3, 4)
MODBUS_ACTION_TIMEOUT_SECONDS = 5.0
MODBUS_INTER_FRAME_GAP_SECONDS = 0.005
CIRCUIT_LOAD_MAPS = {
    0x05: 1,
    0x06: 2,
    0x07: 3,
    0x08: 4,
}
CONFIG_FILE_EXTENSION = ".zcf"
CONFIG_FILENAME = "configuration.zcf"
CONFIG_FILENAME_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


def sanitize_config_filename(filename: str) -> str:
    normalized = str(filename or "").replace("\\", "/")
    name = normalized.rsplit("/", 1)[-1].strip()
    name = CONFIG_FILENAME_SAFE_CHARS.sub("_", name)
    return name.strip(". ")


def save_zcf_config_file(uploaded_file, target_dir: str | os.PathLike[str] | None = None) -> Path:
    filename = sanitize_config_filename(getattr(uploaded_file, "filename", ""))
    if not filename:
        raise ValueError("Choose a .zcf configuration file to upload")
    if Path(filename).suffix.lower() != CONFIG_FILE_EXTENSION:
        raise ValueError("Configuration file must have a .zcf extension")

    destination_dir = Path(target_dir) if target_dir is not None else Path.cwd()
    destination_path = destination_dir / CONFIG_FILENAME
    uploaded_file.save(str(destination_path))
    return destination_path

# ---------------- CAN TRANSPORT ----------------


@dataclass
class CANFrame:
    ID: int
    Data: bytes
    DataLen: int
    ExternFlag: int = 1


class CANTransport(Protocol):
    def open(self): ...
    def send(self, can_id, data: bytes): ...
    def recv(self): ...
    def close(self): ...


class SocketCANTransport:
    def __init__(self, channel: str = "awlink0"):
        try:
            import can  # type: ignore
        except ImportError as exc:
            raise RuntimeError("python-can is required for SocketCAN backend") from exc
        self._can = can
        self.channel = channel
        self.bus = None
        self.auto_up = os.getenv("CAN_AUTO_UP", "1").strip().lower() not in {"0", "false", "no"}
        self.bitrate = int(os.getenv("CAN_BITRATE", "250000"))
        self.send_timeout_seconds = float(os.getenv("CAN_SEND_TIMEOUT_SECONDS", "0.2"))
        self.send_retry_delay_seconds = float(os.getenv("CAN_SEND_RETRY_DELAY_SECONDS", "0.05"))
        self.max_send_retries = int(os.getenv("CAN_SEND_MAX_RETRIES", "40"))

    def _is_link_up(self) -> bool:
        try:
            result = subprocess.run(
                ["ip", "link", "show", self.channel],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception:
            return False
        return " state UP " in result.stdout or "<UP," in result.stdout

    def _run_cmd(self, cmd: list[str], check: bool = True):
        subprocess.run(cmd, check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _ensure_link_up(self):
        if self._is_link_up():
            return
        if not self.auto_up:
            raise RuntimeError(f"SocketCAN interface {self.channel} is DOWN and CAN_AUTO_UP is disabled")
        print(f"SocketCAN interface {self.channel} is DOWN; resetting and bringing up at {self.bitrate} bps")
        self._run_cmd(["ip", "link", "set", self.channel, "down"], check=False)
        self._run_cmd(["ip", "link", "set", self.channel, "type", "can", "bitrate", str(self.bitrate)], check=False)
        self._run_cmd(["ip", "link", "set", self.channel, "up"], check=True)
        if not self._is_link_up():
            raise RuntimeError(f"SocketCAN interface {self.channel} remains DOWN after bring-up attempt")
        print(f"SocketCAN interface {self.channel} is now UP")

    def open(self):
        self._ensure_link_up()
        self.bus = self._can.interface.Bus(channel=self.channel, interface="socketcan")
        print(f"SocketCAN opened on interface: {self.channel}")

    def send(self, can_id, data: bytes):
        if self.bus is None:
            raise RuntimeError("SocketCAN bus is not opened")
        msg = self._can.Message(arbitration_id=can_id, data=data, is_extended_id=True)
        retries = 0
        while True:
            try:
                self.bus.send(msg, timeout=self.send_timeout_seconds)
                return
            except self._can.CanOperationError as exc:
                error_code = getattr(exc, "error_code", None)
                if error_code != ENOBUFS and "No buffer space available" not in str(exc):
                    raise
                retries += 1
                if retries >= self.max_send_retries:
                    raise RuntimeError(
                        f"SocketCAN send failed after {retries} retries (interface={self.channel})"
                    ) from exc
                time.sleep(self.send_retry_delay_seconds)

    def recv(self):
        if self.bus is None:
            raise RuntimeError("SocketCAN bus is not opened")
        frames = []
        while True:
            msg = self.bus.recv(timeout=0)
            if msg is None:
                break
            payload = bytes(msg.data)[:8]
            frames.append(
                CANFrame(
                    ID=int(msg.arbitration_id),
                    Data=payload,
                    DataLen=len(payload),
                    ExternFlag=1 if msg.is_extended_id else 0,
                )
            )
        return frames

    def close(self):
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass
            self.bus = None


def resolve_serial_port(configured_port: str) -> str:
    if configured_port.startswith("/dev/"):
        return configured_port

    alias_map = {
        "COM8": os.getenv("SERIAL_LINUX_DEFAULT_PORT", MODBUS_DEFAULT_COM_PORT),
    }
    env_alias = os.getenv("SERIAL_COM_ALIAS_MAP", "")
    for entry in env_alias.split(","):
        if "=" not in entry:
            continue
        alias, mapped = entry.split("=", 1)
        alias_map[alias.strip().upper()] = mapped.strip()

    return alias_map.get(configured_port.strip().upper(), configured_port)


def select_can_transport() -> tuple[CANTransport, dict[str, str]]:
    channel = os.getenv("CAN_CHANNEL", "awlink0").strip() or "awlink0"
    transport = SocketCANTransport(channel)
    details = {"backend": "socketcan", "can_interface": channel}
    print(f"Startup CAN selection: backend=socketcan, interface={channel}")
    return transport, details

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
    dev: CANTransport
    state: int = 0
    authenticated: bool = True
    on_switch_event: Optional[Callable[[int, bool], None]] = None
    logger: Optional["AppLogger"] = None
    czone_dip_switch: int = CZONE_DIP_SWITCH_DEFAULT
    heartbeat_value: int = CZONE_HEARTBEAT_VALUE_DEFAULT
    pending_commands: dict[int, int] | None = None
    circuit_load_maps: dict[int, int | tuple[int, ...] | list[int] | set[int]] | None = None

    def __post_init__(self):
        self.czone_dip_switch = self._normalize_byte(self.czone_dip_switch)
        self.heartbeat_value = self._normalize_byte(self.heartbeat_value)
        self._log("CZone startup: pre-authenticated for immediate display sync")
        self._log(
            f"Identity: NMEA2000 SRC={SRC}, CZone DIP Switch={self.czone_dip_switch}, "
            f"Heartbeat Value=0x{self.heartbeat_value:02X}"
        )
        if self.pending_commands is None:
            self.pending_commands = {}
        if self.circuit_load_maps is None:
            self.circuit_load_maps = dict(CIRCUIT_LOAD_MAPS)
        self.circuit_load_maps = self._normalize_circuit_load_maps(self.circuit_load_maps)
        # Default currents are 0.0 A for all outputs at startup.
        # Outputs 5-6 remain reserved and fixed at 0.0 A.
        self.output_current_tenths = {idx: 0 for idx in range(1, OUTPUT_COUNT + 1)}
        self.output_block_overrides: dict[int, tuple[int, int, int, int]] = {}

    def _normalize_current_tenths(self, value: int) -> int:
        return max(0, min(255, int(value)))

    @staticmethod
    def _normalize_byte(value: int) -> int:
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
        try:
            self.dev.send(n2k_id(priority, pgn, SRC), data)
        except Exception as exc:
            self._log(f"CAN TX failed for PGN {pgn}: {exc}")

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
        if self.logger:
            self.logger.log(message)
        else:
            print(message)

    def get_switch_states(self):
        states = []
        for switch_id in range(1, 5):
            mask = 1 << (switch_id - 1)
            states.append(bool(self.state & mask))
        return states

    def heartbeat(self):
        if self.authenticated:
            data = u16(CZONE_MESSAGE) + bytes([
                self.czone_dip_switch,
                self.heartbeat_value,
                self.state,
                0x00,
                0x00,
                0x00,
            ])
        else:
            data = (
                u16(CZONE_MESSAGE)
                + bytes([0xFF, self.heartbeat_value, self.heartbeat_value])
                + u16(0)
                + bytes([0])
            )

        self.send(PGN_65284, data)

    def detailed_status(self):
        # Legacy PGN 130817 layout: header + six 4-byte output blocks = 28 bytes.
        # Keep this byte layout the same as the original implementation; circuit
        # mapping only decides which local output changes, not how feedback is encoded.
        # Current mapping discovered from bench testing:
        # O1 -> block1 b0, O2 -> block1 b3, O3 -> block2 b2, O4 -> block3 b1,
        # then +3 byte stride for outputs 5 and 6.
        payload = bytearray(u16(CZONE_MESSAGE) + bytes([0x00, self.czone_dip_switch]))
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

    @staticmethod
    def _state_mask_for_output(output_index: int) -> int:
        return 1 << (output_index - 1)

    def _set_switch(self, switch_code: int, is_on: bool) -> bool:
        if not (0x05 <= switch_code <= 0x08):
            return False
        bit = switch_code - 0x05
        mask = 1 << bit
        self.state = (self.state | mask) if is_on else (self.state & ~mask)
        return bool(self.state & mask)

    def _set_output(self, output_index: int, is_on: bool) -> bool:
        return self._set_switch(0x04 + output_index, is_on)

    @staticmethod
    def _normalize_circuit_load_maps(
        circuit_load_maps: dict[int, int | tuple[int, ...] | list[int] | set[int]],
    ) -> dict[int, tuple[int, ...]]:
        normalized = {}
        for circuit_code, load_indexes in circuit_load_maps.items():
            if isinstance(load_indexes, int):
                load_tuple = (load_indexes,)
            else:
                load_tuple = tuple(load_indexes)
            normalized[int(circuit_code)] = tuple(
                int(load_index) for load_index in load_tuple if 1 <= int(load_index) <= 4
            )
        return normalized

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

        circuit_code = data[2]
        cmd = data[6]
        load_indexes = self.circuit_load_maps.get(circuit_code, ())

        if not load_indexes:
            self._log(
                f"RX 65280 ignored: unmapped circuit 0x{circuit_code:02X} from CZone ID {sender_text}"
            )
            return

        if cmd in (0xF1, 0xF2):
            # Stage command and apply on commit (0x40) to match CZone sequencing.
            self.pending_commands[circuit_code] = cmd
            desired = cmd == 0xF1
            loads_text = ",".join(str(load_index) for load_index in load_indexes)
            self._log(
                f"RX 65280 staged: circuit=0x{circuit_code:02X} loads={loads_text} "
                f"desired={'ON' if desired else 'OFF'}"
            )
        elif cmd in (0x40, 0x42):
            staged = self.pending_commands.get(circuit_code)
            desired = staged == 0xF1
            updated_states = []
            should_emit_switch_event = staged in (0xF1, 0xF2)
            if should_emit_switch_event:
                for output_index in load_indexes:
                    is_on = self._set_output(output_index, desired)
                    updated_states.append((output_index, is_on))
                self.pending_commands.pop(circuit_code, None)
            else:
                # Match the original Arduino sketch behavior: 0x40/0x42 is the
                # end/ack phase, not a switch-change request by itself. Report the
                # current mapped output state but do not drive Modbus/event outputs.
                for output_index in load_indexes:
                    is_on = bool(self.state & self._state_mask_for_output(output_index))
                    updated_states.append((output_index, is_on))

            if should_emit_switch_event and self.on_switch_event:
                for output_index, is_on in updated_states:
                    self.on_switch_event(0x04 + output_index, is_on)

            states_text = ", ".join(
                f"Output {output_index}={'ON' if is_on else 'OFF'}"
                for output_index, is_on in updated_states
            )
            self._log(f"Circuit 0x{circuit_code:02X} -> {states_text}")
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




class ModbusBreakerBridge:
    def __init__(self, port: str = MODBUS_DEFAULT_COM_PORT, baudrate: int = MODBUS_BAUDRATE):
        self.port = port
        self.baudrate = int(baudrate)
        self.ser: Optional[serial.Serial] = None
        self._bus_lock = threading.Lock()
        self._last_transaction_at = 0.0

    def connect(self):
        if self.ser and self.ser.is_open:
            return
        self.ser = serial.Serial(self.port, self.baudrate, timeout=0.2)
        print(f"Modbus serial connected: port={self.port}, baudrate={self.baudrate}")

    @staticmethod
    def _crc16(data: bytes) -> int:
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc

    def _send_frame(self, frame: bytes, response_size: int) -> bytes:
        with self._bus_lock:
            self.connect()

            # Modbus RTU requires request/response sequencing and an inter-frame silent interval.
            elapsed = time.monotonic() - self._last_transaction_at
            if elapsed < MODBUS_INTER_FRAME_GAP_SECONDS:
                time.sleep(MODBUS_INTER_FRAME_GAP_SECONDS - elapsed)

            # Drop stale bytes so each request reads only its own response.
            self.ser.reset_input_buffer()

            crc = self._crc16(frame)
            tx = frame + struct.pack("<H", crc)
            self.ser.write(tx)
            self.ser.flush()

            response = bytearray()
            deadline = time.monotonic() + float(self.ser.timeout or 0.2)
            while len(response) < response_size and time.monotonic() < deadline:
                chunk = self.ser.read(response_size - len(response))
                if chunk:
                    response.extend(chunk)

            self._last_transaction_at = time.monotonic()
            return bytes(response)

    def _valid_crc(self, response: bytes) -> bool:
        if len(response) < 4:
            return False
        payload = response[:-2]
        received_crc = struct.unpack("<H", response[-2:])[0]
        return self._crc16(payload) == received_crc

    def read_status(self, slave_id: int) -> Optional[int]:
        frame = bytes([slave_id, 0x03, (MODBUS_STATUS_REGISTER >> 8) & 0xFF, MODBUS_STATUS_REGISTER & 0xFF, 0x00, 0x01])
        response = self._send_frame(frame, response_size=7)
        if len(response) < 7 or response[0] != slave_id or response[1] != 0x03 or response[2] != 0x02:
            return None
        if not self._valid_crc(response):
            return None
        return (response[3] << 8) | response[4]

    def write_command(self, slave_id: int, value: int) -> bool:
        frame = bytes([slave_id, 0x06, (MODBUS_STATUS_REGISTER >> 8) & 0xFF, MODBUS_STATUS_REGISTER & 0xFF, (value >> 8) & 0xFF, value & 0xFF])
        response = self._send_frame(frame, response_size=8)
        if len(response) < 8 or response[0] != slave_id or response[1] != 0x06:
            return False
        return self._valid_crc(response) and response[:6] == frame

    def close(self):
        if self.ser and self.ser.is_open:
            self.ser.close()




class AppLogger:
    def __init__(self, max_entries: int = 500):
        self.max_entries = max_entries
        self.entries: list[str] = []
        self._lock = threading.Lock()

    def log(self, message: str):
        print(message)
        timestamped = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}"
        with self._lock:
            self.entries.append(timestamped)
            if len(self.entries) > self.max_entries:
                self.entries = self.entries[-self.max_entries :]

    def get_entries(self) -> list[str]:
        with self._lock:
            return list(self.entries)


class CZoneWebServer:
    def __init__(self, czone: CZone, logger: AppLogger, host: str = '0.0.0.0', port: int = 8080):
        self.czone = czone
        self.logger = logger
        self.host = host
        self.port = port
        self.app = Flask(__name__)
        self._setup_routes()

    def _setup_routes(self):
        @self.app.get('/')
        def index():
            return """<!doctype html>
<html><head><meta charset='utf-8'><title>CZone Emulator</title>
<style>body{font-family:Arial,sans-serif;margin:16px}button{padding:8px;margin:4px}.on{background:#2e7d32;color:#fff}.off{background:#c62828;color:#fff}.card{border:1px solid #ccc;border-radius:8px;padding:10px;margin-bottom:10px}label{display:inline-block;min-width:110px}input[type=number]{width:90px}pre{background:#111;color:#d7ffd7;padding:8px;white-space:pre-wrap;line-height:1.25em}</style></head>
<body><h2>CZone OI Emulator (Headless Web)</h2>
<div class='card'><div id='states'></div><div id='mapping'></div></div>
<div class='card'><h3>Switches</h3><div id='buttons'></div></div>
<div class='card'><h3>Output currents (A)</h3><div id='currents'></div></div>
<div class='card'><h3>Configuration file</h3><form id='config_form'><input id='config_file' name='config_file' type='file' accept='.zcf'><button type='submit'>Load .zcf</button></form><div id='config_status'></div></div>
<div class='card'><h3>Logs</h3><pre id='logs'></pre></div>
<script>
let uiInit=false;
function ensureUi(s){
if(uiInit) return;
const b=document.getElementById('buttons');
s.switch_states.forEach((_,i)=>{const id=i+1;const btn=document.createElement('button');btn.id=`sw_${id}`;btn.onclick=()=>fetch('/api/toggle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({switch_id:id})}).then(refresh);b.appendChild(btn);});
const c=document.getElementById('currents');
Object.entries(s.output_currents).forEach(([k,val])=>{const row=document.createElement('div');row.style.margin='5px 0';row.innerHTML=`<label>Output ${k}</label><input step='0.1' min='0' max='25.5' type='number' id='out_${k}' value='${Number(val).toFixed(1)}'><button id='apply_${k}'>Apply</button>`;row.querySelector('button').onclick=()=>{const amps=parseFloat(document.getElementById(`out_${k}`).value||'0');fetch('/api/output_current',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({output_index:Number(k),amps:amps})}).then(refresh)};c.appendChild(row);});
const form=document.getElementById('config_form');
form.onsubmit=async (event)=>{event.preventDefault();const status=document.getElementById('config_status');const input=document.getElementById('config_file');if(!input.files.length){status.textContent='Choose a .zcf file first.';return;}const data=new FormData();data.append('config_file',input.files[0]);const response=await fetch('/api/config/upload',{method:'POST',body:data});const result=await response.json();status.textContent=response.ok?`Saved ${result.filename} to ${result.path}`:(result.error||'Upload failed');if(response.ok){input.value='';refresh();}};
uiInit=true;
}
async function refresh(){const s=await (await fetch('/api/state')).json();const l=await (await fetch('/api/logs')).json();ensureUi(s);
const st=s.switch_states.map((v,i)=>`S${i+1}: ${v?'ON':'OFF'}`).join(' | ');document.getElementById('states').innerText=`DIP: ${s.czone_dip_switch}   ${st}`;
const mapLines=Object.entries(s.mappings).map(([circuit,loads])=>`${circuit}: `+loads.join(', '));document.getElementById('mapping').innerText='Circuit load mappings:\\n'+mapLines.join('\\n');
s.switch_states.forEach((v,i)=>{const id=i+1;const btn=document.getElementById(`sw_${id}`);btn.className=v?'on':'off';btn.textContent=`Toggle S${id} (${v?'ON':'OFF'})`;});
Object.entries(s.output_currents).forEach(([k,val])=>{const input=document.getElementById(`out_${k}`);if(document.activeElement!==input){input.value=Number(val).toFixed(1);}});
document.getElementById('logs').textContent=(l.logs||[]).slice(-50).join('\\n');}
setInterval(refresh,1000);refresh();
</script></body></html>
"""

        @self.app.get('/api/state')
        def state():
            return jsonify({
                'switch_states': self.czone.get_switch_states(),
                'czone_dip_switch': self.czone.czone_dip_switch,
                'output_currents': {
                    str(output_index): self.czone.get_output_current(output_index)
                    for output_index in range(1, ADJUSTABLE_OUTPUT_COUNT + 1)
                },
                'mappings': {
                    f"0x{circuit_code:02X}": list(load_indexes)
                    for circuit_code, load_indexes in sorted(self.czone.circuit_load_maps.items())
                },
            })

        @self.app.post('/api/toggle')
        def toggle():
            payload = request.get_json(silent=True) or {}
            switch_id = int(payload.get('switch_id', 0))
            if not (1 <= switch_id <= 4):
                return jsonify({'error': 'switch_id must be 1..4'}), 400
            current = self.czone.get_switch_states()[switch_id - 1]
            updated = self.czone._set_output(switch_id, not current)
            self.logger.log(f"Web switch {switch_id} -> {'ON' if updated else 'OFF'}")
            if self.czone.on_switch_event:
                self.czone.on_switch_event(0x04 + switch_id, updated)
            self.czone.heartbeat()
            self.czone.detailed_status()
            return jsonify({'switch_id': switch_id, 'is_on': updated})

        @self.app.post('/api/output_current')
        def set_output_current():
            payload = request.get_json(silent=True) or {}
            output_index = int(payload.get('output_index', 0))
            amps = float(payload.get('amps', 0.0))
            if not (1 <= output_index <= ADJUSTABLE_OUTPUT_COUNT):
                return jsonify({'error': f'output_index must be 1..{ADJUSTABLE_OUTPUT_COUNT}'}), 400
            self.czone.set_output_current(output_index, amps)
            normalized = self.czone.get_output_current(output_index)
            self.logger.log(f"Web output {output_index} current -> {normalized:.1f} A")
            self.czone.detailed_status()
            return jsonify({'output_index': output_index, 'amps': normalized})

        @self.app.post('/api/config/upload')
        def upload_config_file():
            uploaded_file = request.files.get('config_file')
            if uploaded_file is None:
                return jsonify({'error': 'Choose a .zcf configuration file to upload'}), 400
            try:
                saved_path = save_zcf_config_file(uploaded_file)
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
            self.logger.log(f"Web configuration file loaded: {saved_path.name} saved to {saved_path}")
            return jsonify({'filename': saved_path.name, 'path': str(saved_path)})

        @self.app.get('/api/logs')
        def logs():
            return jsonify({'logs': self.logger.get_entries()})

    def run(self):
        self.logger.log(f'Web server listening on http://{self.host}:{self.port}')
        self.app.run(host=self.host, port=self.port, debug=False, use_reloader=False)


class CZoneHeadless:
    def __init__(self, czone: CZone, logger: AppLogger, modbus_port: str, modbus_baudrate: int):
        self.czone = czone
        self.logger = logger
        self.modbus_bridge = ModbusBreakerBridge(port=modbus_port, baudrate=modbus_baudrate)
        self.modbus_enabled = True
        self.modbus_requests: Queue = Queue()
        self.modbus_events: Queue = Queue()
        self.pending_modbus_actions: dict[int, dict[str, float | bool | None]] = {}
        self._modbus_running = True
        self._modbus_thread = threading.Thread(target=self._modbus_worker, daemon=True)
        self._modbus_thread.start()
        self.czone.on_switch_event = self.record_switch_event
        self.last_heartbeat = time.time()
        self.last_status = time.time()
        self.last_n2k_identity = time.time() - 60

    def _modbus_worker(self):
        while self._modbus_running:
            try:
                req = self.modbus_requests.get(timeout=MODBUS_POLL_INTERVAL_SECONDS)
            except Empty:
                req = None

            if req:
                action, switch_id, is_on = req
                if action == "write":
                    try:
                        ok = self.modbus_bridge.write_command(switch_id, 2 if is_on else 1)
                    except Exception as exc:
                        ok = False
                        self.modbus_events.put(("error", f"Modbus write error breaker {switch_id}: {exc}"))
                    self.modbus_events.put(("write_ack", switch_id, is_on, ok))

            for switch_id in MODBUS_SWITCH_IDS:
                try:
                    value = self.modbus_bridge.read_status(switch_id)
                except Exception as exc:
                    self.modbus_events.put(("error", f"Modbus poll error: {exc}"))
                    self.modbus_enabled = False
                    return
                self.modbus_events.put(("status", switch_id, value))
            time.sleep(MODBUS_POLL_INTERVAL_SECONDS)

    def _process_modbus_events(self):
        while True:
            try:
                event = self.modbus_events.get_nowait()
            except Empty:
                break

            kind = event[0]
            if kind == "error":
                self.logger.log(event[1])
                continue
            if kind == "write_ack":
                _, switch_id, is_on, ok = event
                if not ok:
                    self.logger.log(f"Modbus write failed for breaker {switch_id}")
                continue
            _, switch_id, value = event
            if value is None:
                continue
            is_on = value == 2
            pending = self.pending_modbus_actions.get(switch_id)
            if pending:
                pending["last_polled"] = is_on
                desired = bool(pending["desired"])
                if is_on == desired:
                    self.pending_modbus_actions.pop(switch_id, None)
                else:
                    continue

            before = bool(self.czone.state & self.czone._state_mask_for_output(switch_id))
            after = self.czone._set_output(switch_id, is_on)
            if after != before:
                source_state = {1: "OPEN", 2: "CLOSED", 3: "TRIPPED/LOCKED"}.get(value, f"RAW={value}")
                self.logger.log(f"Modbus breaker {switch_id} status -> {source_state}")
                self.czone.heartbeat()
                self.czone.detailed_status()

    def _check_modbus_timeouts(self):
        now = time.time()
        expired = [sid for sid, info in self.pending_modbus_actions.items() if now > float(info["deadline"])]
        for switch_id in expired:
            info = self.pending_modbus_actions.pop(switch_id)
            desired = bool(info["desired"])
            last_polled = info.get("last_polled")

            if desired:
                final_state = False if last_polled is not True else True
            else:
                final_state = True if last_polled is True else False

            before = bool(self.czone.state & self.czone._state_mask_for_output(switch_id))
            after = self.czone._set_output(switch_id, final_state)
            if after != before:
                self.logger.log(f"Modbus timeout on breaker {switch_id}; final virtual state {'ON' if after else 'OFF'}")
                self.czone.heartbeat()
                self.czone.detailed_status()

    def _send_modbus_command(self, switch_id: int, is_on: bool):
        if not self.modbus_enabled:
            return
        self.pending_modbus_actions[switch_id] = {"desired": is_on, "deadline": time.time() + MODBUS_ACTION_TIMEOUT_SECONDS, "last_polled": None}
        self.modbus_requests.put(("write", switch_id, is_on))

    def record_switch_event(self, switch_code: int, is_on: bool):
        switch_id = (switch_code - 0x05) + 1
        state_text = "ON" if is_on else "OFF"
        self.logger.log(f"Switch {switch_id} (code 0x{switch_code:02X}) -> {state_text}")
        self._send_modbus_command(switch_id, is_on)

    def run(self):
        self.logger.log("CZone emulator headless mode running...")
        self.czone.address_claim()
        self.czone.product_information()
        while True:
            self.czone.process_rx()
            self._process_modbus_events()
            self._check_modbus_timeouts()
            now = time.time()
            if now - self.last_heartbeat > 2:
                self.last_heartbeat = now
                self.czone.heartbeat()
            if now - self.last_n2k_identity > 60:
                self.last_n2k_identity = now
                self.czone.address_claim()
                self.czone.product_information()
            if now - self.last_status > 2:
                self.last_status = now
                self.czone.detailed_status()
            time.sleep(0.05)

    def close(self):
        self._modbus_running = False
        if hasattr(self, "_modbus_thread"):
            self._modbus_thread.join(timeout=0.5)
        self.modbus_bridge.close()


# ---------------- MAIN ----------------


def main():
    transport, can_details = select_can_transport()
    configured_port = os.getenv("SERIAL_PORT", MODBUS_DEFAULT_COM_PORT)
    resolved_port = resolve_serial_port(configured_port)
    modbus_baudrate = int(os.getenv("SERIAL_BAUDRATE", str(MODBUS_BAUDRATE)))

    try:
        transport.open()
    except Exception as exc:
        raise RuntimeError(
            f"SocketCAN open failed (interface={can_details['can_interface']}, "
            f"serial_port={resolved_port}, baudrate={modbus_baudrate}): {exc}"
        ) from exc

    print(
        f"Startup serial selection: configured={configured_port}, resolved={resolved_port}, "
        f"baudrate={modbus_baudrate}"
    )

    try:
        logger = AppLogger()
        czone = CZone(transport, logger=logger)
        web_host = os.getenv("WEB_HOST", "0.0.0.0")
        web_port = int(os.getenv("WEB_PORT", "8080"))
        web_server = CZoneWebServer(czone, logger=logger, host=web_host, port=web_port)
        web_thread = threading.Thread(target=web_server.run, daemon=True)
        web_thread.start()
        print("Startup UI mode: headless web server only")

        # Push presence/status frames immediately after CAN open so reconnects do not
        # wait for the first periodic timer.
        for _ in range(3):
            czone.address_claim()
            czone.product_information()
            czone.heartbeat()
            czone.detailed_status()
            time.sleep(0.1)

        app = CZoneHeadless(czone, logger=logger, modbus_port=resolved_port, modbus_baudrate=modbus_baudrate)
        try:
            app.run()
        finally:
            app.close()
    finally:
        try:
            transport.close()
        except Exception:
            pass

if __name__ == "__main__":
    main()

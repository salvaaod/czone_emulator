import argparse
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

import serial

DEFAULT_PORT = "COM8"
DEFAULT_BAUDRATE = 9600
STATUS_REGISTER = 0x8000
POLL_INTERVAL_SECONDS = 0.5
SWITCH_IDS = (1, 2, 3, 4)

STATUS_OPEN = 1
STATUS_CLOSED = 2
STATUS_LOCKED = 3

COMMAND_OPEN = 1
COMMAND_CLOSE = 2


@dataclass(frozen=True)
class BreakerStatus:
    device_id: int
    value: Optional[int]

    @property
    def label(self) -> str:
        if self.value == STATUS_OPEN:
            return "OPEN"
        if self.value == STATUS_CLOSED:
            return "CLOSED"
        if self.value == STATUS_LOCKED:
            return "TRIPPED/LOCKED"
        if self.value is None:
            return "NO RESPONSE"
        return f"UNKNOWN({self.value})"


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


class ModbusRtuClient:
    def __init__(self, port: str = DEFAULT_PORT, baudrate: int = DEFAULT_BAUDRATE, timeout: float = 0.2):
        self._serial = serial.Serial(port=port, baudrate=baudrate, timeout=timeout)
        self._lock = threading.Lock()

    def close(self):
        self._serial.close()

    def _exchange(self, payload: bytes, response_size: int) -> bytes:
        frame = payload + struct.pack("<H", crc16(payload))
        with self._lock:
            self._serial.reset_input_buffer()
            self._serial.write(frame)
            return self._serial.read(response_size)

    def read_register(self, slave_id: int, address: int) -> Optional[int]:
        payload = bytes([
            slave_id,
            0x03,
            (address >> 8) & 0xFF,
            address & 0xFF,
            0x00,
            0x01,
        ])
        response = self._exchange(payload, 7)
        if len(response) < 7 or response[0] != slave_id or response[1] != 0x03:
            return None
        return (response[3] << 8) | response[4]

    def write_register(self, slave_id: int, address: int, value: int) -> bool:
        payload = bytes([
            slave_id,
            0x06,
            (address >> 8) & 0xFF,
            address & 0xFF,
            (value >> 8) & 0xFF,
            value & 0xFF,
        ])
        response = self._exchange(payload, 8)
        return len(response) == 8 and response[:6] == payload


class BreakerController:
    def __init__(
        self,
        client: ModbusRtuClient,
        on_status_change: Optional[Callable[[BreakerStatus], None]] = None,
        poll_interval: float = POLL_INTERVAL_SECONDS,
    ):
        self._client = client
        self._poll_interval = poll_interval
        self._on_status_change = on_status_change
        self._last_status: dict[int, Optional[int]] = {device_id: None for device_id in SWITCH_IDS}
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _poll_loop(self):
        while not self._stop_event.is_set():
            for device_id in SWITCH_IDS:
                value = self._client.read_register(device_id, STATUS_REGISTER)
                if self._last_status[device_id] != value:
                    self._last_status[device_id] = value
                    if self._on_status_change:
                        self._on_status_change(BreakerStatus(device_id, value))
            time.sleep(self._poll_interval)

    def command_open(self, switch_number: int) -> bool:
        return self._command(switch_number, COMMAND_OPEN)

    def command_close(self, switch_number: int) -> bool:
        return self._command(switch_number, COMMAND_CLOSE)

    def _command(self, switch_number: int, value: int) -> bool:
        if switch_number not in SWITCH_IDS:
            raise ValueError("switch_number must be 1..4")
        ok = self._client.write_register(switch_number, STATUS_REGISTER, value)
        if ok:
            new_value = self._client.read_register(switch_number, STATUS_REGISTER)
            self._last_status[switch_number] = new_value
            if self._on_status_change:
                self._on_status_change(BreakerStatus(switch_number, new_value))
        return ok


def main():
    parser = argparse.ArgumentParser(description="4-breaker Modbus RTU controller")
    parser.add_argument("--port", default=DEFAULT_PORT, help="Serial COM port (default: COM8)")
    parser.add_argument("--baudrate", type=int, default=DEFAULT_BAUDRATE)
    parser.add_argument("--switch", type=int, choices=SWITCH_IDS, help="Switch number (1..4)")
    parser.add_argument("cmd", choices=["open", "close", "monitor"])
    args = parser.parse_args()

    client = ModbusRtuClient(port=args.port, baudrate=args.baudrate)

    def print_status(status: BreakerStatus):
        print(f"S{status.device_id}: {status.label}")

    controller = BreakerController(client=client, on_status_change=print_status)

    try:
        if args.cmd == "monitor":
            controller.start()
            while True:
                time.sleep(1)
        elif args.cmd == "open":
            if args.switch is None:
                raise SystemExit("--switch is required for open")
            print("OK" if controller.command_open(args.switch) else "FAILED")
        elif args.cmd == "close":
            if args.switch is None:
                raise SystemExit("--switch is required for close")
            print("OK" if controller.command_close(args.switch) else "FAILED")
    finally:
        controller.stop()
        client.close()


if __name__ == "__main__":
    main()

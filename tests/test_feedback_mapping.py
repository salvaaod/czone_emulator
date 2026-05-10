import sys
import types
import unittest

flask_stub = types.ModuleType("flask")
flask_stub.Flask = lambda *args, **kwargs: None
flask_stub.jsonify = lambda obj=None, *args, **kwargs: obj
flask_stub.request = types.SimpleNamespace(get_json=lambda **kwargs: {})
sys.modules.setdefault("flask", flask_stub)

serial_stub = types.ModuleType("serial")
serial_stub.Serial = object
sys.modules.setdefault("serial", serial_stub)

from czone_emulator import CZONE_MESSAGE, CZone, PGN_130817, PGN_65284, SRC, n2k_id, parse_pgn


class DummyTransport:
    def __init__(self):
        self.sent = []

    def send(self, can_id, data):
        self.sent.append((can_id, data))

    def recv(self):
        return []


class FeedbackMappingTest(unittest.TestCase):
    def setUp(self):
        self.transport = DummyTransport()
        self.czone = CZone(self.transport)
        self.command_prefix = CZONE_MESSAGE.to_bytes(2, "little")

    def send_circuit_command(self, circuit_code, command):
        self.czone.handle_command(0xAA, self.command_prefix + bytes([circuit_code, 0x00, 0x00, 0xAA, command]))

    def latest_heartbeat_state(self):
        heartbeat_payloads = [data for can_id, data in self.transport.sent if parse_pgn(can_id) == PGN_65284]
        self.assertTrue(heartbeat_payloads)
        return heartbeat_payloads[-1][4]

    def latest_detailed_status_payload(self):
        combined_payload = bytearray()
        frames = [(can_id, data) for can_id, data in self.transport.sent if can_id == n2k_id(7, PGN_130817, SRC)]
        self.assertTrue(frames)
        for _, frame in frames:
            if frame[0] & 0x1F == 0:
                combined_payload = bytearray(frame[2:])
            else:
                combined_payload.extend(frame[1:])
        return combined_payload

    def test_default_circuit_codes_map_to_configured_switch_status_bits(self):
        for circuit_code, switch_number in ((0x05, 1), (0x06, 2), (0x07, 1), (0x08, 2), (0x09, 3), (0x0A, 4)):
            with self.subTest(circuit_code=circuit_code, switch_number=switch_number):
                self.czone.state = 0
                self.transport.sent.clear()
                self.send_circuit_command(circuit_code, 0xF1)
                self.send_circuit_command(circuit_code, 0x40)
                self.assertEqual(self.latest_heartbeat_state(), 1 << (switch_number - 1))
                self.assertEqual(
                    self.czone.get_switch_states(),
                    [idx == switch_number for idx in range(1, 5)],
                )

    def test_arbitrary_circuit_codes_map_to_configured_switches(self):
        self.czone = CZone(self.transport, circuit_load_maps={0x10: 1, 0x11: 2})

        for circuit_code, switch_number in ((0x10, 1), (0x11, 2)):
            with self.subTest(circuit_code=circuit_code, switch_number=switch_number):
                self.czone.state = 0
                self.transport.sent.clear()
                self.send_circuit_command(circuit_code, 0xF1)
                self.send_circuit_command(circuit_code, 0x40)
                self.assertEqual(self.latest_heartbeat_state(), 1 << (switch_number - 1))
                self.assertEqual(
                    self.czone.get_switch_states(),
                    [idx == switch_number for idx in range(1, 5)],
                )

    def test_current_feedback_keeps_original_pgn_byte_positions(self):
        for output_index, amps in ((1, 1.1), (2, 2.2), (3, 3.3), (4, 4.4)):
            self.czone.set_output_current(output_index, amps)

        self.transport.sent.clear()
        self.czone.detailed_status()
        payload = self.latest_detailed_status_payload()
        output_bytes = payload[4:]

        self.assertEqual(len(payload), 28)
        self.assertEqual(output_bytes[0], 11)
        self.assertEqual(output_bytes[3], 22)
        self.assertEqual(output_bytes[6], 33)
        self.assertEqual(output_bytes[9], 44)


if __name__ == "__main__":
    unittest.main()

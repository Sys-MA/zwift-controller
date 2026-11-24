import unittest
from openbikecontrol import Bridge, button_packet, BUTTON_IDS

class PacketTests(unittest.TestCase):
    def test_buttons(self):
        self.assertEqual(len(set(BUTTON_IDS.values())), 10)
        for name, code in BUTTON_IDS.items():
            self.assertEqual(button_packet(name, True), bytes([1, code, 1]))
            self.assertEqual(button_packet(name, False), bytes([1, code, 0]))

    def test_status(self):
        bridge = Bridge(lambda msg: None)
        self.assertEqual(bridge.status(), bytes([2, 255, 0]))
        bridge.set_ready(True)
        bridge.set_battery(100)
        self.assertEqual(bridge.status(), bytes([2, 100, 1]))

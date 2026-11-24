import unittest
from zwift_controller import decode_buttons, changes, BUTTONS

class ButtonTests(unittest.TestCase):
    def test_all_buttons(self):
        for mask, name in BUTTONS.items():
            value = 0xFFFFFFFF ^ mask
            data = bytearray([0x23, 0x08])
            while value >= 128:
                data.append((value & 127) | 128)
                value >>= 7
            data.append(value)
            with self.subTest(button=name):
                self.assertEqual(decode_buttons(data), mask)
                self.assertEqual(changes(0, mask), [(name, True, mask)])
                self.assertEqual(changes(mask, mask), [])
                self.assertEqual(changes(mask, 0), [(name, False, mask)])

    def test_neutral(self):
        self.assertEqual(decode_buttons(bytes.fromhex("23 08 FF FF FF FF 0F")), 0)

    def test_invalid(self):
        self.assertIsNone(decode_buttons(bytes.fromhex("23 08 FF")))
        self.assertIsNone(decode_buttons(b"RideOn"))

if __name__ == "__main__":
    unittest.main()

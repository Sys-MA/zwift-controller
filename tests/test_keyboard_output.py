import unittest
from keyboard_output import key_code, DEFAULT_KEYS

class KeyboardTests(unittest.TestCase):
    def test_gears(self):
        self.assertEqual(key_code(DEFAULT_KEYS["PLUS"]), 0x4B)
        self.assertEqual(key_code(DEFAULT_KEYS["MOINS"]), 0x49)

    def test_special_keys(self):
        self.assertEqual(key_code("LEFT"), 0x25)
        self.assertEqual(key_code("enter"), 0x0D)

    def test_disabled_and_invalid(self):
        self.assertIsNone(key_code(None))
        with self.assertRaises(ValueError):
            key_code("INVALID")

import unittest
from heart_rate import decode_bpm


class HeartRateTests(unittest.TestCase):
    def test_8_bit(self):
        self.assertEqual(decode_bpm(bytes([0, 142])), 142)

    def test_16_bit(self):
        self.assertEqual(decode_bpm(bytes([1, 4, 1])), 260)

    def test_contact(self):
        self.assertIsNone(decode_bpm(bytes([4, 120])))
        self.assertEqual(decode_bpm(bytes([6, 120])), 120)

    def test_invalid(self):
        for data in (b"", b"\x00", b"\x01\x01", b"\x00\x00", b"\x01\xff\xff"):
            self.assertIsNone(decode_bpm(data))


if __name__ == "__main__":
    unittest.main()

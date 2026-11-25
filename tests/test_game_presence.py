import os
import unittest

from game_presence import GamePolicy, game_running

class GamePolicyTests(unittest.TestCase):
    def test_open_close_with_grace(self):
        p = GamePolicy()
        self.assertFalse(p.desired)
        p.observe(False, 0)
        self.assertFalse(p.desired)
        p.observe(True, 2)
        self.assertTrue(p.desired)
        p.observe(False, 5)
        p.observe(False, 14)
        self.assertTrue(p.desired)
        p.observe(False, 15)
        self.assertFalse(p.desired)

    def test_transient_absence(self):
        p = GamePolicy()
        p.observe(True, 0)
        p.observe(False, 2)
        p.observe(True, 4)
        p.observe(False, 9)
        p.observe(False, 15)
        self.assertTrue(p.desired)

    def test_unknown_does_not_disconnect(self):
        p = GamePolicy()
        p.observe(True, 0)
        p.observe(False, 2)
        p.observe(None, 15)
        p.observe(False, 16)
        self.assertTrue(p.desired)
        p.observe(False, 26)
        self.assertFalse(p.desired)

    def test_manual_stop_until_next_open(self):
        p = GamePolicy()
        p.observe(True, 0)
        p.manual(False)
        p.observe(True, 2)
        self.assertFalse(p.desired)
        p.observe(False, 3)
        p.observe(False, 13)
        p.observe(True, 14)
        self.assertTrue(p.desired)

    def test_manual_start_when_closed(self):
        p = GamePolicy()
        p.observe(False, 0)
        p.manual(True)
        p.observe(False, 3)
        self.assertTrue(p.desired)
        p.observe(True, 4)
        p.observe(False, 5)
        p.observe(False, 15)
        self.assertFalse(p.desired)

    @unittest.skipUnless(os.name == "nt", "Windows only")
    def test_windows_snapshot(self):
        self.assertIsInstance(game_running(), bool)

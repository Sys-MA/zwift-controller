import unittest
from keyboard_output import key_code, DEFAULT_KEYS, migrate_mapping

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

    def test_new_defaults(self):
        self.assertEqual(DEFAULT_KEYS, {"PLUS":"K","MOINS":"I","GAUCHE":"A","DROITE":"D","HAUT":"U","BAS":"C","A":"1","B":"I","Y":"2","Z":"3"})

    def test_migration(self):
        config = {"keyboard_mapping":{"GAUCHE":"LEFT","B":"Q","Z":None}}
        migrate_mapping(config)
        self.assertEqual(config["keyboard_mapping"], {"GAUCHE":"A","B":"Q","Z":None})
        config["keyboard_mapping"]["GAUCHE"] = "LEFT"
        migrate_mapping(config)
        self.assertEqual(config["keyboard_mapping"]["GAUCHE"], "LEFT")

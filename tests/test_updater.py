import unittest
from updater import select_release, version_tuple, restart_environment

class UpdaterTests(unittest.TestCase):
    def release(self, tag="v0.8.1", **extra):
        return {"tag_name": tag, "assets": [{
            "name": "ZwiftController.exe",
            "browser_download_url": "https://github.com/Sys-MA/zwift-controller/releases/download/v0.8.1/ZwiftController.exe",
            "digest": "sha256:" + "a"*64
        }], **extra}

    def test_versions(self):
        self.assertEqual(version_tuple("v0.10.0"), (0,10,0))
        self.assertIsNone(version_tuple("v0.8.0-beta"))
        self.assertIsNone(select_release(self.release("v0.8.0"), "0.8.0"))
        self.assertIsNone(select_release(self.release("v0.7.2"), "0.8.0"))

    def test_stable_only(self):
        self.assertIsNone(select_release(self.release(prerelease=True), "0.8.0"))
        self.assertIsNone(select_release(self.release(draft=True), "0.8.0"))
        self.assertTrue(select_release(self.release(), "0.8.0")["installable"])

    def test_missing_checksum(self):
        release = self.release()
        release["assets"][0]["digest"] = None
        self.assertFalse(select_release(release, "0.8.0")["installable"])

    def test_unexpected_url(self):
        release = self.release()
        release["assets"][0]["browser_download_url"] = "https://example.org/ZwiftController.exe"
        with self.assertRaises(ValueError):
            select_release(release, "0.8.0")

    def test_restart_environment(self):
        original = {"PATH":"C:/Windows", "_PYI_APPLICATION_HOME_DIR":"old-temp",
                    "_PYI_ARCHIVE_FILE":"old.exe", "_PYI_PARENT_PROCESS_LEVEL":"1",
                    "_MEIPASS2":"old-temp"}
        env = restart_environment(original)
        self.assertEqual(env, {"PATH":"C:/Windows", "PYINSTALLER_RESET_ENVIRONMENT":"1"})
        self.assertIn("_PYI_APPLICATION_HOME_DIR", original)

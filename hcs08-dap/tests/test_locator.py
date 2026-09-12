"""Platform-specific USBDM file names and search locations (no hardware, no library needed)."""
import os
import platform
import unittest
from unittest import mock

from hcs08dap import usbdm as U


class LocatorTests(unittest.TestCase):
    def _on(self, system, machine="x86_64", bits="64bit"):
        return mock.patch.multiple(U, _SYSTEM=system), mock.patch.object(platform, "machine", return_value=machine), \
            mock.patch.object(platform, "architecture", return_value=(bits, ""))

    def test_windows_names(self):
        with self._on("Windows")[0], self._on("Windows")[1], self._on("Windows")[2]:
            self.assertEqual(U.library_name(), "usbdm.4.dll")
            self.assertEqual(U.programmer_name(), "UsbdmFlashProgrammer.exe")
            self.assertEqual(U.multiarch_dirs(), ["x86_64-win-gnu", "i386-win-gnu"])
            cands = U._candidates("lib", U.library_name(), None)
            self.assertTrue(any(c.replace("\\", "/").endswith("PackageFiles/bin/x86_64-win-gnu/usbdm.4.dll") for c in cands))

    def test_linux_names(self):
        with self._on("Linux", machine="aarch64")[0], self._on("Linux", machine="aarch64")[1]:
            self.assertEqual(U.library_name(), "libusbdm.so.4")
            self.assertEqual(U.programmer_name(), "UsbdmFlashProgrammer")
            self.assertEqual(U.multiarch_dirs()[0], "aarch64-linux-gnu")
            cands = U._candidates("lib", U.library_name(), None)
            self.assertIn("/usr/lib/aarch64-linux-gnu/usbdm/libusbdm.so.4", cands)
            self.assertIn("/usr/bin/UsbdmFlashProgrammer", U._candidates("bin", U.programmer_name(), None))

    def test_macos_names(self):
        with self._on("Darwin", machine="arm64")[0], self._on("Darwin", machine="arm64")[1]:
            self.assertEqual(U.library_name(), "libusbdm.4.dylib")
            self.assertEqual(U.multiarch_dirs(), ["arm64-apple-darwin"])
            self.assertIn("/usr/local/usbdm/lib/libusbdm.4.dylib", U._candidates("lib", U.library_name(), None))

    def test_explicit_and_env(self):
        with self._on("Linux")[0], self._on("Linux")[1]:
            self.assertEqual(U._candidates("lib", "libusbdm.so.4", "/x/libfoo.so"), ["/x/libfoo.so"])
            with mock.patch.dict(os.environ, {"USBDM_HOME": "/opt/usbdm"}):
                cands = U._candidates("lib", "libusbdm.so.4", None)
                self.assertEqual(cands[0], "/opt/usbdm/lib/x86_64-linux-gnu/libusbdm.so.4")
                self.assertIn("/opt/usbdm/lib/libusbdm.so.4", cands)

    def test_missing_library_lists_candidates(self):
        with mock.patch.object(U, "_candidates", return_value=["/nowhere/libusbdm.so.4"]):
            with self.assertRaises(FileNotFoundError) as cm:
                U.find_library()
            self.assertIn("/nowhere/libusbdm.so.4", str(cm.exception))

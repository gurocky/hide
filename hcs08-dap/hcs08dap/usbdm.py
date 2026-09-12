"""ctypes binding for the USBDM C API and the platform-specific lookup of its host library."""
from __future__ import annotations

import ctypes
import os
import platform

# ---- constants from USBDM_API.h
T_HCS08 = 1
MS_BYTE = 1
RESET_SPECIAL = 0
RESET_NORMAL = 1
RESET_DEFAULT = 7 << 2
HCS08_REG_A = 8
HCS08_REG_CCR = 9
HCS08_REG_PC = 0xB
HCS08_REG_HX = 0xC
HCS08_REG_SP = 0xF
HCS08_DREG_BKPT = 0
BDCSCR_ENBDM = 0x80
BDCSCR_BDMACT = 0x40
BDCSCR_BKPTEN = 0x20
BDCSCR_FTS = 0x10
BDM_TARGET_VDD = {"off": 0, "3V3": 1, "5V": 2}
AUTOCONNECT_STATUS = 1
CS_DEFAULT = 0xFF

ERROR_NAMES = {
    0: "OK", 1: "illegal params", 2: "fail", 3: "busy", 4: "illegal command", 5: "no connection",
    6: "overrun", 7: "CF illegal command", 8: "device open failed", 9: "USB error", 12: "unknown target",
    13: "no Tx routine", 14: "no Rx routine", 15: "BDM not ready", 16: "BDM not enabled",
    17: "target reset failed", 18: "target sync failed", 19: "target sync timeout", 20: "no target Vdd",
    21: "Vdd not removed", 22: "Vdd not present", 23: "Vdd wrong level", 24: "BDM in use",
    25: "no USB", 26: "ACK timeout", 27: "target reset timeout", 28: "target not halted",
    36: "no USBDM device located",
}


class UsbdmError(RuntimeError):
    def __init__(self, what: str, rc: int):
        super().__init__(f"{what}: USBDM rc {rc} ({ERROR_NAMES.get(rc, '?')})")
        self.rc = rc


class ExtendedOptions(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint),
        ("targetType", ctypes.c_int),
        ("targetVdd", ctypes.c_int),
        ("cycleVddOnReset", ctypes.c_bool),
        ("cycleVddOnConnect", ctypes.c_bool),
        ("leaveTargetPowered", ctypes.c_bool),
        ("autoReconnect", ctypes.c_int),
        ("guessSpeed", ctypes.c_bool),
        ("bdmClockSource", ctypes.c_int),
        ("useResetSignal", ctypes.c_bool),
        ("maskInterrupts", ctypes.c_bool),
        ("interfaceFrequency", ctypes.c_uint),
        ("usePSTSignals", ctypes.c_bool),
        ("powerOffDuration", ctypes.c_uint),
        ("powerOnRecoveryInterval", ctypes.c_uint),
        ("resetDuration", ctypes.c_uint),
        ("resetReleaseInterval", ctypes.c_uint),
        ("resetRecoveryInterval", ctypes.c_uint),
        ("hcs08sbdfrAddress", ctypes.c_uint),
    ]


# ---- locating the USBDM host library and command-line programmer
#
# USBDM names and places its files differently on each platform:
#
#   platform   host library         programmer                 build tree (usbdm/PackageFiles)      installed
#   Windows    usbdm.4.dll          UsbdmFlashProgrammer.exe   bin/<arch>-win-gnu/ (dll + exe)      registry HKLM\SOFTWARE\pgo\USBDM
#   Linux      libusbdm.so.4        UsbdmFlashProgrammer       lib/<multiarch>/, bin/<multiarch>/   /usr/lib/<multiarch>/usbdm, /usr/bin
#   macOS      libusbdm.4.dylib     UsbdmFlashProgrammer       lib/<arch>-apple-darwin/, bin/...    <prefix>/lib, <prefix>/bin (InstallMacOS)
#
# Search order: explicit launch attribute (usbdmLib / usbdmProgrammer, file or directory) > $USBDM_HOME
# (a PackageFiles-style build tree or an installed prefix) > the platform's installed locations > PATH
# (programmer only). A developer using an uninstalled build tree points USBDM_HOME at its PackageFiles.

_SYSTEM = platform.system()


def library_name() -> str:
    if _SYSTEM == "Windows":
        return "usbdm.4.dll"
    if _SYSTEM == "Darwin":
        return "libusbdm.4.dylib"
    return "libusbdm.so.4"


def programmer_name() -> str:
    return "UsbdmFlashProgrammer.exe" if _SYSTEM == "Windows" else "UsbdmFlashProgrammer"


def multiarch_dirs() -> list[str]:
    """Directory names USBDM's makefiles use for the current platform (MULTIARCH in Common.mk)."""
    m = platform.machine().lower()
    if _SYSTEM == "Windows":
        return ["x86_64-win-gnu", "i386-win-gnu"] if platform.architecture()[0] == "64bit" else ["i386-win-gnu"]
    if _SYSTEM == "Darwin":
        return [f"{m}-apple-darwin"]
    m = {"amd64": "x86_64", "arm64": "aarch64", "armv7l": "arm"}.get(m, m)
    return [f"{m}-linux-gnu", f"{m}-linux-gnueabihf"]


def _windows_install_dir() -> str | None:
    try:
        import winreg  # type: ignore
    except ImportError:
        return None
    for access in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\pgo\USBDM", 0, winreg.KEY_READ | access) as k:
                value, _ = winreg.QueryValueEx(k, "InstallationDirectory")
                if value:
                    return value
        except OSError:
            continue
    return None


def package_roots() -> list[str]:
    """PackageFiles-style trees (lib/<arch>, bin/<arch>) or install prefixes (lib/, bin/): only $USBDM_HOME."""
    env = os.environ.get("USBDM_HOME")
    return [os.path.normpath(env)] if env else []


def _installed_dirs(kind: str) -> list[str]:
    """Installed locations for kind = 'lib' or 'bin'."""
    if _SYSTEM == "Windows":
        d = _windows_install_dir()
        dirs = [d] if d else []
        for pf in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
            if pf and os.path.isdir(os.path.join(pf, "pgo")):
                dirs += [os.path.join(pf, "pgo", n) for n in sorted(os.listdir(os.path.join(pf, "pgo")), reverse=True)]
        return dirs
    if _SYSTEM == "Darwin":
        return [os.path.join(p, kind) for p in ("/usr/local/usbdm", "/opt/local", "/usr/local")] + (
            ["/opt/local/lib/usbdm", "/usr/local/lib/usbdm"] if kind == "lib" else [])
    if kind == "lib":
        return [f"/usr/lib/{a}/usbdm" for a in multiarch_dirs()] + ["/usr/lib/usbdm", "/usr/local/lib/usbdm", "/usr/lib"]
    return ["/usr/bin", "/usr/local/bin"]


def _candidates(kind: str, name: str, explicit: str | None) -> list[str]:
    dirs: list[str] = []
    if explicit:
        if os.path.isdir(explicit):
            dirs.append(explicit)
        else:
            return [explicit]
    for root in package_roots():
        for arch in multiarch_dirs():
            dirs.append(os.path.join(root, kind, arch))
            if _SYSTEM == "Windows":
                dirs.append(os.path.join(root, "bin", arch))   # Windows build puts DLLs next to the executables
        dirs.append(os.path.join(root, kind))                  # an installed prefix given as USBDM_HOME
    dirs += _installed_dirs(kind)
    seen, out = set(), []
    for d in dirs:
        full = os.path.normpath(os.path.join(d, name))
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out


def _find(kind: str, name: str, explicit: str | None) -> str | None:
    for c in _candidates(kind, name, explicit):
        if os.path.isfile(c):
            return c
    return None


def find_library(explicit: str | None = None) -> str:
    path = _find("lib", library_name(), explicit)
    if path is None:
        raise FileNotFoundError("找不到 USBDM 库 %s，已查找：\n  %s\n可在 launch 配置里用 usbdmLib 指定，或设置 USBDM_HOME"
                                % (library_name(), "\n  ".join(_candidates("lib", library_name(), explicit))))
    return path


def find_programmer(explicit: str | None = None) -> str:
    path = _find("bin", programmer_name(), explicit)
    if path is None:
        import shutil
        path = shutil.which(programmer_name())
    if path is None:
        raise FileNotFoundError("找不到 %s，已查找：\n  %s\n可在 launch 配置里用 usbdmProgrammer 指定，或设置 USBDM_HOME"
                                % (programmer_name(), "\n  ".join(_candidates("bin", programmer_name(), explicit))))
    return path


def default_library() -> str:
    return find_library()


def default_programmer() -> str:
    return find_programmer()


def load_library(path: str) -> ctypes.CDLL:
    """Load the host library; on Windows the API is stdcall and dependent DLLs live next to it."""
    if _SYSTEM == "Windows":
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(os.path.dirname(os.path.abspath(path)))
        return ctypes.WinDLL(path)
    return ctypes.CDLL(path)


class UsbdmApi:
    """Thin wrapper: every call raises UsbdmError on a non-zero return code."""

    def __init__(self, library: str | None = None):
        self.path = find_library(library)
        self.lib = load_library(self.path)
        L = self.lib
        L.USBDM_Init.restype = ctypes.c_int
        L.USBDM_Exit.restype = ctypes.c_int
        L.USBDM_FindDevices.argtypes = [ctypes.POINTER(ctypes.c_uint)]
        L.USBDM_Open.argtypes = [ctypes.c_ubyte]
        L.USBDM_Close.restype = ctypes.c_int
        L.USBDM_GetDefaultExtendedOptions.argtypes = [ctypes.POINTER(ExtendedOptions)]
        L.USBDM_SetExtendedOptions.argtypes = [ctypes.POINTER(ExtendedOptions)]
        L.USBDM_SetTargetType.argtypes = [ctypes.c_int]
        L.USBDM_Connect.restype = ctypes.c_int
        L.USBDM_TargetReset.argtypes = [ctypes.c_int]
        L.USBDM_TargetGo.restype = ctypes.c_int
        L.USBDM_TargetHalt.restype = ctypes.c_int
        L.USBDM_TargetStep.restype = ctypes.c_int
        L.USBDM_ReadStatusReg.argtypes = [ctypes.POINTER(ctypes.c_ulong)]
        L.USBDM_WriteControlReg.argtypes = [ctypes.c_uint]
        L.USBDM_ReadReg.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)]
        L.USBDM_WriteReg.argtypes = [ctypes.c_uint, ctypes.c_ulong]
        L.USBDM_ReadDReg.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)]
        L.USBDM_WriteDReg.argtypes = [ctypes.c_uint, ctypes.c_ulong]
        L.USBDM_ReadMemory.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte)]
        L.USBDM_WriteMemory.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_ubyte)]
        L.USBDM_GetBDMSerialNumber.argtypes = [ctypes.POINTER(ctypes.c_char_p)]

    def _chk(self, what: str, rc: int) -> None:
        if rc != 0:
            raise UsbdmError(what, rc)

    # ---- session
    def init(self) -> None:
        self._chk("USBDM_Init", self.lib.USBDM_Init())

    def exit(self) -> None:
        self.lib.USBDM_Exit()

    def find_devices(self) -> int:
        n = ctypes.c_uint(0)
        rc = self.lib.USBDM_FindDevices(ctypes.byref(n))
        if rc == 36:
            return 0
        self._chk("USBDM_FindDevices", rc)
        return n.value

    def open(self, index: int = 0) -> None:
        self._chk("USBDM_Open", self.lib.USBDM_Open(index))

    def close(self) -> None:
        self.lib.USBDM_Close()

    def serial_number(self) -> str:
        p = ctypes.c_char_p()
        if self.lib.USBDM_GetBDMSerialNumber(ctypes.byref(p)) == 0 and p.value:
            return p.value.decode(errors="replace")
        return ""

    def configure(self, vdd: str = "off", mask_interrupts: bool = True) -> None:
        opt = ExtendedOptions()
        opt.size = ctypes.sizeof(ExtendedOptions)
        opt.targetType = T_HCS08
        self._chk("USBDM_GetDefaultExtendedOptions", self.lib.USBDM_GetDefaultExtendedOptions(ctypes.byref(opt)))
        opt.targetType = T_HCS08
        opt.targetVdd = BDM_TARGET_VDD.get(vdd, 0)
        opt.autoReconnect = AUTOCONNECT_STATUS
        opt.maskInterrupts = mask_interrupts
        opt.leaveTargetPowered = True
        self._chk("USBDM_SetExtendedOptions", self.lib.USBDM_SetExtendedOptions(ctypes.byref(opt)))
        self._chk("USBDM_SetTargetType", self.lib.USBDM_SetTargetType(T_HCS08))

    def connect(self) -> None:
        self._chk("USBDM_Connect", self.lib.USBDM_Connect())

    # ---- execution
    def reset(self, mode: int = RESET_SPECIAL | RESET_DEFAULT) -> None:
        self._chk("USBDM_TargetReset", self.lib.USBDM_TargetReset(mode))

    def go(self) -> None:
        self._chk("USBDM_TargetGo", self.lib.USBDM_TargetGo())

    def halt(self) -> None:
        self._chk("USBDM_TargetHalt", self.lib.USBDM_TargetHalt())

    def step(self) -> None:
        self._chk("USBDM_TargetStep", self.lib.USBDM_TargetStep())

    def status_reg(self) -> int:
        v = ctypes.c_ulong(0)
        self._chk("USBDM_ReadStatusReg", self.lib.USBDM_ReadStatusReg(ctypes.byref(v)))
        return v.value

    def write_control_reg(self, value: int) -> None:
        self._chk("USBDM_WriteControlReg", self.lib.USBDM_WriteControlReg(value))

    # ---- registers / memory
    def read_reg(self, reg: int) -> int:
        v = ctypes.c_ulong(0)
        self._chk("USBDM_ReadReg", self.lib.USBDM_ReadReg(reg, ctypes.byref(v)))
        return v.value

    def write_reg(self, reg: int, value: int) -> None:
        self._chk("USBDM_WriteReg", self.lib.USBDM_WriteReg(reg, value))

    def write_dreg(self, reg: int, value: int) -> None:
        self._chk("USBDM_WriteDReg", self.lib.USBDM_WriteDReg(reg, value))

    def read_memory(self, address: int, count: int) -> bytes:
        buf = (ctypes.c_ubyte * max(count, 1))()
        self._chk("USBDM_ReadMemory", self.lib.USBDM_ReadMemory(MS_BYTE, count, address, buf))
        return bytes(buf[:count])

    def write_memory(self, address: int, data: bytes) -> None:
        buf = (ctypes.c_ubyte * max(len(data), 1))(*data)
        self._chk("USBDM_WriteMemory", self.lib.USBDM_WriteMemory(MS_BYTE, len(data), address, buf))

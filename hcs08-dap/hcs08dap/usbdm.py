"""ctypes binding for the USBDM C API (libusbdm built from the usbdm checkout next to HIDE)."""
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


def default_package_dir() -> str:
    """$USBDM_HOME, else ``usbdm/PackageFiles`` of a usbdm checkout next to this directory or next to HIDE."""
    env = os.environ.get("USBDM_HOME")
    if env:
        return env
    here = os.path.dirname(os.path.realpath(__file__))
    candidates = [
        os.path.join(here, "..", "..", "usbdm", "PackageFiles"),        # <hide>/usbdm
        os.path.join(here, "..", "..", "..", "usbdm", "PackageFiles"),  # sibling of <hide>
    ]
    for c in candidates:
        if os.path.isdir(c):
            return os.path.normpath(c)
    return os.path.normpath(candidates[0])


def default_arch() -> str:
    return f"{platform.machine()}-apple-darwin" if platform.system() == "Darwin" else f"{platform.machine()}-linux-gnu"


def default_library() -> str:
    d = default_package_dir()
    if platform.system() == "Darwin":
        return os.path.join(d, "lib", default_arch(), "libusbdm.4.dylib")
    return os.path.join(d, "lib", default_arch(), "libusbdm.so.4")


def default_programmer() -> str:
    return os.path.join(default_package_dir(), "bin", default_arch(), "UsbdmFlashProgrammer")


class UsbdmApi:
    """Thin wrapper: every call raises UsbdmError on a non-zero return code."""

    def __init__(self, library: str | None = None):
        self.path = library or default_library()
        self.lib = ctypes.CDLL(self.path)
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

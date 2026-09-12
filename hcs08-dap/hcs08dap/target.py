"""HCS08 target control: the real thing over USBDM, and a fake for protocol tests."""
from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

from . import usbdm as U

# MC9S08 DBG module (AW60: 0x1810..0x1818); comparators A/B give two more instruction breakpoints
DBGCA = 0x1810
DBGCB = 0x1812
DBGC = 0x1816
DBGT = 0x1817
DBGS = 0x1818
DBGC_DBGEN, DBGC_ARM, DBGC_TAG, DBGC_BRKEN = 0x80, 0x40, 0x20, 0x10
DBGT_BEGIN = 0x40
DBGT_TRG_A_ONLY, DBGT_TRG_A_OR_B = 0x0, 0x1

MAX_BREAKPOINTS = 3   # BDC BKPT + DBG comparator A + DBG comparator B


@dataclass
class Regs:
    pc: int
    sp: int
    hx: int
    a: int
    ccr: int


class Target:
    """What the adapter needs from a target. All calls are synchronous."""

    max_breakpoints = MAX_BREAKPOINTS

    def connect(self) -> str: ...
    def close(self) -> None: ...
    def reset_halt(self) -> None: ...
    def halt(self) -> None: ...
    def go(self) -> None: ...
    def step(self) -> None: ...
    def is_halted(self) -> bool: ...
    def regs(self) -> Regs: ...
    def write_reg(self, name: str, value: int) -> None: ...
    def read_mem(self, addr: int, n: int) -> bytes: ...
    def write_mem(self, addr: int, data: bytes) -> None: ...
    def set_breakpoints(self, addrs: list[int]) -> None: ...
    def program(self, s19: str, device: str, vdd: str, log) -> None: ...


# ============================================================================ real target


class UsbdmTarget(Target):
    def __init__(self, library: str | None = None, programmer: str | None = None, vdd: str = "off", bdm_index: int = 0):
        self.api = U.UsbdmApi(library)
        self.programmer = programmer            # resolved lazily by flash(); None = search the default locations
        self.vdd = vdd
        self.bdm_index = bdm_index
        self._bps: list[int] = []
        self._open = False

    # ---- session
    def connect(self) -> str:
        self.api.init()
        n = self.api.find_devices()
        if n == 0:
            raise RuntimeError("没有找到 USBDM 探头（USB 未枚举到 USBDM 设备）")
        self.api.open(self.bdm_index)
        self._open = True
        self.api.configure(self.vdd, mask_interrupts=True)
        self.api.connect()
        return f"USBDM {self.api.serial_number()} ({n} 个探头)"

    def close(self) -> None:
        if self._open:
            try:
                self.api.close()
            finally:
                self._open = False
        self.api.exit()

    def reset_halt(self) -> None:
        self.api.reset(U.RESET_SPECIAL | U.RESET_DEFAULT)
        self.api.connect()
        self._disarm()

    # ---- execution
    def halt(self) -> None:
        self.api.halt()

    def go(self) -> None:
        pc = self.api.read_reg(U.HCS08_REG_PC)
        if pc in self._bps:
            # tag breakpoints trigger on fetch: step off the breakpoint first, with breakpoints disabled
            self._disarm()
            self.api.step()
        self._arm()
        self.api.go()

    def step(self) -> None:
        self._disarm()
        self.api.step()

    def is_halted(self) -> bool:
        return bool(self.api.status_reg() & U.BDCSCR_BDMACT)

    # ---- state
    def regs(self) -> Regs:
        r = self.api.read_reg
        return Regs(pc=r(U.HCS08_REG_PC), sp=r(U.HCS08_REG_SP), hx=r(U.HCS08_REG_HX), a=r(U.HCS08_REG_A), ccr=r(U.HCS08_REG_CCR))

    def write_reg(self, name: str, value: int) -> None:
        reg = {"pc": U.HCS08_REG_PC, "sp": U.HCS08_REG_SP, "hx": U.HCS08_REG_HX, "a": U.HCS08_REG_A, "ccr": U.HCS08_REG_CCR}[name]
        self.api.write_reg(reg, value)

    def read_mem(self, addr: int, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            chunk = min(n, 128)
            out += self.api.read_memory(addr, chunk)
            addr += chunk
            n -= chunk
        return bytes(out)

    def write_mem(self, addr: int, data: bytes) -> None:
        for off in range(0, len(data), 128):
            self.api.write_memory(addr + off, data[off:off + 128])

    # ---- breakpoints: slot 0 = BDC BKPT register, slots 1/2 = DBG comparators A/B (tag mode)
    def set_breakpoints(self, addrs: list[int]) -> None:
        self._bps = list(dict.fromkeys(addrs))[:MAX_BREAKPOINTS]

    def _arm(self) -> None:
        bps = self._bps
        if bps:
            self.api.write_dreg(U.HCS08_DREG_BKPT, bps[0])
            self.api.write_control_reg(U.BDCSCR_ENBDM | U.BDCSCR_BKPTEN)      # FTS = 0: tag (opcode fetch)
        else:
            self.api.write_control_reg(U.BDCSCR_ENBDM)
        if len(bps) > 1:
            self.api.write_memory(DBGC, bytes([0]))                            # disarm while reconfiguring
            self.api.write_memory(DBGCA, bps[1].to_bytes(2, "big"))
            trg = DBGT_TRG_A_ONLY
            if len(bps) > 2:
                self.api.write_memory(DBGCB, bps[2].to_bytes(2, "big"))
                trg = DBGT_TRG_A_OR_B
            self.api.write_memory(DBGT, bytes([DBGT_BEGIN | trg]))
            self.api.write_memory(DBGC, bytes([DBGC_DBGEN | DBGC_ARM | DBGC_TAG | DBGC_BRKEN]))
        else:
            self.api.write_memory(DBGC, bytes([0]))

    def _disarm(self) -> None:
        self.api.write_control_reg(U.BDCSCR_ENBDM)
        try:
            self.api.write_memory(DBGC, bytes([0]))
        except U.UsbdmError:
            pass

    # ---- flash
    def program(self, s19: str, device: str, vdd: str, log) -> None:
        """Flash with the USBDM command-line programmer (it needs the USB device to itself)."""
        programmer = U.find_programmer(self.programmer)
        was_open = self._open
        if was_open:
            self.api.close()
            self._open = False
        cmd = [programmer, "-target=HCS08", f"-device={device}", "-erase=Mass", "-program", "-verify", "-verbose", "-execute", s19]
        if vdd in ("3V3", "5V"):
            cmd.insert(3, f"-vdd={vdd}")
        log(" ".join(cmd))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        for line in (proc.stdout + proc.stderr).splitlines():
            log("  " + line)
        if proc.returncode != 0:
            raise RuntimeError(f"烧录失败，UsbdmFlashProgrammer 返回 {proc.returncode}")
        if was_open:
            time.sleep(0.3)
            self.api.open(self.bdm_index)
            self._open = True
            self.api.configure(self.vdd, mask_interrupts=True)
            self.api.connect()


# ============================================================================ fake target


class FakeTarget(Target):
    """A target that only 'executes' line starts: go() runs to the next breakpoint, step() to the
    next known address. Enough to exercise the adapter without hardware."""

    def __init__(self, addresses: list[int], reset_pc: int, sp: int = 0x086F):
        self.addresses = sorted(set(addresses))
        self.mem = bytearray(0x10000)
        self.r = Regs(pc=reset_pc, sp=sp, hx=0, a=0, ccr=0x68)
        self.halted = True
        self.bps: list[int] = []
        self.programmed: list[str] = []
        self.reset_pc = reset_pc
        self.log: list[str] = []

    def connect(self) -> str:
        self.log.append("connect")
        return "FakeTarget"

    def close(self) -> None:
        self.log.append("close")

    def reset_halt(self) -> None:
        self.r.pc = self.reset_pc
        self.halted = True

    def halt(self) -> None:
        self.halted = True

    def go(self) -> None:
        self.log.append(f"go@{self.r.pc:04X}")
        later = [b for b in sorted(self.bps) if b > self.r.pc]
        if later:
            self.r.pc = later[0]
        elif self.bps:
            self.r.pc = min(self.bps)
        else:
            self.halted = False     # runs "forever" until halt()
            return
        self.halted = True

    def step(self) -> None:
        later = [a for a in self.addresses if a > self.r.pc]
        self.r.pc = later[0] if later else self.addresses[0]
        self.halted = True

    def is_halted(self) -> bool:
        return self.halted

    def regs(self) -> Regs:
        return Regs(**vars(self.r))

    def write_reg(self, name: str, value: int) -> None:
        setattr(self.r, name, value)

    def read_mem(self, addr: int, n: int) -> bytes:
        return bytes(self.mem[addr:addr + n])

    def write_mem(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data

    def set_breakpoints(self, addrs: list[int]) -> None:
        self.bps = list(addrs)[:MAX_BREAKPOINTS]

    def program(self, s19: str, device: str, vdd: str, log) -> None:
        self.programmed.append(s19)
        log(f"fake program {s19}")

"""Debug Adapter Protocol server for HCS08 (SDCC + USBDM).

Protocol handling is synchronous per request; run control (continue / step) happens on a worker
thread that reports a ``stopped`` event when the target halts.  Everything that touches the target
goes through ``self._lock``.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import traceback
from typing import Any, Callable

from . import cdb as cdbmod
from .target import FakeTarget, Target, UsbdmTarget

THREAD_ID = 1
MAX_LINE_STEPS = 5000
ENDIAN = "big"          # SDCC s08 keeps multi-byte values big-endian

# HCS08 subroutine calls: opcode -> instruction length (used by "step over")
CALL_OPCODES = {0xAD: 2, 0xBD: 2, 0xCD: 3, 0xDD: 3, 0xED: 2, 0xFD: 1}
RTS, RTI = 0x81, 0x80


class DapError(Exception):
    pass


class Adapter:
    def __init__(self, send: Callable[[dict], None], target_factory: Callable[[dict], Target] | None = None, log=None):
        self._send = send
        self._seq = 1
        self._lock = threading.RLock()
        self._log = log or (lambda s: None)
        self._target_factory = target_factory or self._default_target
        self.target: Target | None = None
        self.cdb: cdbmod.Cdb | None = None
        self.args: dict = {}
        self.sources: dict[str, str] = {}          # basename -> full path
        self.user_bps: dict[str, list[int]] = {}   # source path -> addresses
        self._configured = threading.Event()
        self._pause = False
        self._running = False
        self._worker: threading.Thread | None = None
        self._var_refs: dict[int, Any] = {}
        self._next_ref = 1
        self.exited = threading.Event()

    # ------------------------------------------------------------------ transport helpers
    def _out(self, msg: dict) -> None:
        msg["seq"] = self._seq
        self._seq += 1
        self._send(msg)

    def event(self, name: str, body: dict | None = None) -> None:
        self._out({"type": "event", "event": name, "body": body or {}})

    def output(self, text: str, category: str = "console") -> None:
        self.event("output", {"category": category, "output": text if text.endswith("\n") else text + "\n"})

    def handle_message(self, msg: dict) -> None:
        if msg.get("type") != "request":
            return
        cmd = msg.get("command", "")
        args = msg.get("arguments") or {}
        handler = getattr(self, "req_" + cmd, None)
        resp = {"type": "response", "request_seq": msg.get("seq"), "command": cmd, "success": True}
        try:
            if handler is None:
                raise DapError(f"unsupported request: {cmd}")
            body = handler(args)
            if body is not None:
                resp["body"] = body
        except Exception as e:  # noqa: BLE001 - every failure becomes a DAP error response
            self._log("error in %s: %s\n%s" % (cmd, e, traceback.format_exc()))
            resp["success"] = False
            resp["message"] = str(e)
        self._out(resp)
        after = getattr(self, "after_" + cmd, None)
        if after is not None and resp["success"]:
            after(args)

    # ------------------------------------------------------------------ target factory
    @staticmethod
    def _default_target(args: dict) -> Target:
        if args.get("fake"):
            return FakeTarget(addresses=[], reset_pc=0)
        return UsbdmTarget(library=args.get("usbdmLib"), programmer=args.get("usbdmProgrammer"), vdd=args.get("vdd", "off"),
                           bdm_index=int(args.get("bdm", 0)))

    # ------------------------------------------------------------------ requests: session
    def req_initialize(self, args: dict) -> dict:
        return {
            "supportsConfigurationDoneRequest": True,
            "supportsEvaluateForHovers": True,
            "supportsSetVariable": True,
            "supportsReadMemoryRequest": True,
            "supportsWriteMemoryRequest": True,
            "supportsRestartRequest": True,
            "supportTerminateDebuggee": True,
            "supportsTerminateRequest": True,
            "supportsSteppingGranularity": False,
            "supportsDelayedStackTraceLoading": False,
            "exceptionBreakpointFilters": [],
        }

    def _load(self, args: dict, attach: bool) -> None:
        self.args = args
        program = args.get("program")
        cdb_path = args.get("cdb") or (os.path.splitext(program)[0] + ".cdb" if program else None)
        if not cdb_path or not os.path.exists(cdb_path):
            raise DapError(f"找不到 SDCC 调试信息文件 .cdb: {cdb_path}（固件需用 sdcc --debug 编译链接）")
        self.cdb = cdbmod.parse(cdb_path)
        roots = args.get("sourceRoots") or [os.path.dirname(os.path.dirname(os.path.abspath(cdb_path)))]
        self._index_sources(roots)
        self.target = self._target_factory(args)
        if isinstance(self.target, FakeTarget) and not self.target.addresses:
            # the fake "executes" line starts only; reset lands below all code so run-to-main works
            self.target.addresses = sorted(self.cdb._line_starts)
            self.target.reset_pc = 0
            self.target.r.pc = 0
        with self._lock:
            desc = self.target.connect()
        self.output(f"已连接：{desc}；调试信息 {cdb_path}（{len(self.cdb.functions)} 个函数，{len(self.cdb.lines)} 条行记录）")
        if not attach:
            if args.get("flash", True) and program:
                self.output(f"烧录 {program} …")
                with self._lock:
                    self.target.program(program, args.get("device", "MC9S08AW60"), args.get("vdd", "off"), self.output)
                self.output("烧录完成")
            with self._lock:
                self.target.reset_halt()
        else:
            with self._lock:
                if not self.target.is_halted():
                    self.target.halt()

    def req_launch(self, args: dict) -> None:
        self._load(args, attach=False)

    def req_attach(self, args: dict) -> None:
        self._load(args, attach=True)

    def after_launch(self, args: dict) -> None:
        self.event("initialized")

    after_attach = after_launch

    def req_configurationDone(self, args: dict) -> None:
        self._configured.set()
        if self.args.get("request") == "attach" or self.args.get("attach"):
            self._stopped("pause")
            return
        if self.args.get("stopAtEntry"):
            self._stopped("entry")
            return
        entry = self.cdb.function_named("main") if self.cdb else None
        if entry and entry.start is not None:
            self._run_to_async(entry.start, reason="entry")
        else:
            self._stopped("entry")

    def req_disconnect(self, args: dict) -> None:
        self._shutdown(resume=not args.get("terminateDebuggee", False))

    def req_terminate(self, args: dict) -> None:
        self._shutdown(resume=True)
        self.event("terminated")

    def _shutdown(self, resume: bool) -> None:
        with self._lock:
            if self.target is not None:
                try:
                    if resume:
                        self.target.set_breakpoints([])
                        if self.target.is_halted():
                            self.target.go()
                    self.target.close()
                except Exception as e:  # noqa: BLE001
                    self._log(f"shutdown: {e}")
                self.target = None
        self.exited.set()

    def req_restart(self, args: dict) -> None:
        with self._lock:
            self.target.reset_halt()
        entry = self.cdb.function_named("main")
        if entry and entry.start is not None:
            self._run_to_async(entry.start, reason="entry")
        else:
            self._stopped("entry")

    # ------------------------------------------------------------------ requests: breakpoints
    def req_setBreakpoints(self, args: dict) -> dict:
        source = args.get("source") or {}
        path = source.get("path") or source.get("name") or ""
        requested = args.get("breakpoints") or [{"line": ln} for ln in args.get("lines", [])]
        addrs: list[int] = []
        result = []
        for bp in requested:
            found = self.cdb.address_for_line(path, int(bp["line"]))
            if found is None:
                result.append({"verified": False, "line": bp["line"], "message": "该行没有生成代码"})
                continue
            addr, line = found
            addrs.append(addr)
            result.append({"verified": True, "line": line, "instructionReference": f"0x{addr:04X}"})
        self.user_bps[path] = addrs
        all_bps = self._all_user_bps()
        limit = self.target.max_breakpoints if self.target else 3
        if len(all_bps) > limit:
            # mark the overflow (in request order across files) as unverified
            over = set(all_bps[limit:])
            for r, bp in zip(result, requested):
                if r.get("verified") and int(r.get("instructionReference", "0"), 16) in over:
                    r["verified"] = False
                    r["message"] = f"AW60 只有 {limit} 个硬件断点（BDC + DBG A/B），已超出"
        if self.target is not None and not self._running:
            with self._lock:
                self.target.set_breakpoints(all_bps[:limit])
        return {"breakpoints": result}

    def _all_user_bps(self) -> list[int]:
        out: list[int] = []
        for addrs in self.user_bps.values():
            for a in addrs:
                if a not in out:
                    out.append(a)
        return out

    # ------------------------------------------------------------------ requests: run control
    def req_threads(self, args: dict) -> dict:
        return {"threads": [{"id": THREAD_ID, "name": self.args.get("device", "MC9S08AW60")}]}

    def req_continue(self, args: dict) -> dict:
        self._start_worker(self._do_continue)
        return {"allThreadsContinued": True}

    def req_next(self, args: dict) -> None:
        self._start_worker(lambda: self._do_step_line(step_over=True))

    def req_stepIn(self, args: dict) -> None:
        self._start_worker(lambda: self._do_step_line(step_over=False))

    def req_stepOut(self, args: dict) -> None:
        self._start_worker(self._do_step_out)

    def req_pause(self, args: dict) -> None:
        self._pause = True
        with self._lock:
            if self.target is not None and not self.target.is_halted():
                self.target.halt()

    # ------------------------------------------------------------------ run control internals
    def _start_worker(self, fn: Callable[[], None]) -> None:
        if self._running:
            raise DapError("目标正在运行")
        self._pause = False
        self._running = True
        self.event("continued", {"threadId": THREAD_ID, "allThreadsContinued": True})
        self._worker = threading.Thread(target=self._guard, args=(fn,), daemon=True)
        self._worker.start()

    def _guard(self, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            self._log("worker: %s\n%s" % (e, traceback.format_exc()))
            self.output(f"错误：{e}", "stderr")
            self._running = False
            self._stopped("exception", text=str(e))

    def _stopped(self, reason: str, text: str | None = None) -> None:
        self._running = False
        self._var_refs.clear()
        body = {"reason": reason, "threadId": THREAD_ID, "allThreadsStopped": True}
        if text:
            body["text"] = text
            body["description"] = text
        self.event("stopped", body)

    def _wait_halt(self, timeout: float | None = None) -> bool:
        """Poll until the target halts (breakpoint) or pause is requested. Returns True if halted."""
        t0 = time.time()
        while True:
            with self._lock:
                if self.target is None:
                    return False
                if self.target.is_halted():
                    return True
            if self._pause:
                with self._lock:
                    if self.target is not None and not self.target.is_halted():
                        self.target.halt()
                return True
            if timeout is not None and time.time() - t0 > timeout:
                return False
            time.sleep(0.02)

    def _do_continue(self) -> None:
        with self._lock:
            self.target.set_breakpoints(self._all_user_bps()[: self.target.max_breakpoints])
            self.target.go()
        self._wait_halt()
        self._stopped("pause" if self._pause else "breakpoint")

    def _run_to_async(self, addr: int, reason: str) -> None:
        def run() -> None:
            self._run_to(addr)
            self._stopped("pause" if self._pause else reason)
        self._start_worker(run)

    def _run_to(self, addr: int) -> None:
        """Run with a temporary breakpoint at ``addr`` (borrowing the last hardware slot if needed)."""
        with self._lock:
            bps = self._all_user_bps()[: self.target.max_breakpoints - 1]
            if addr not in bps:
                bps.append(addr)
            self.target.set_breakpoints(bps)
            self.target.go()
        self._wait_halt()

    def _pc(self) -> int:
        with self._lock:
            return self.target.regs().pc

    def _do_step_line(self, step_over: bool) -> None:
        cdb = self.cdb
        pc = self._pc()
        start_func = cdb.function_at(pc)
        start_line = cdb.line_at(pc)
        start_key = (start_line.file, start_line.line) if start_line else None
        for _ in range(MAX_LINE_STEPS):
            if self._pause:
                break
            with self._lock:
                opcode = self.target.read_mem(pc, 1)[0]
            if step_over and opcode in CALL_OPCODES:
                self._run_to(pc + CALL_OPCODES[opcode])
            else:
                with self._lock:
                    self.target.step()
            pc = self._pc()
            func = cdb.function_at(pc)
            if func is None:
                continue                      # startup code / library without line info: keep going
            if func is not start_func and step_over:
                # returned to the caller (or landed in an interrupt handler while stepping over)
                if cdb.is_line_start(pc) or opcode in (RTS, RTI):
                    break
                continue
            if not cdb.is_line_start(pc):
                continue
            rec = cdb.line_at(pc)
            if rec is None or (rec.file, rec.line) != start_key or func is not start_func:
                break
        self._stopped("pause" if self._pause else "step")

    def _do_step_out(self) -> None:
        ret = self._return_address()
        if ret is None:
            self.output("找不到返回地址，改为单步", "stderr")
            self._do_step_line(step_over=True)
            return
        self._run_to(ret)
        self._stopped("pause" if self._pause else "step")

    def _return_address(self) -> int | None:
        frames = self._unwind()
        return frames[1][0] if len(frames) > 1 else None

    # ------------------------------------------------------------------ stack
    def _unwind(self, limit: int = 6) -> list[tuple[int, cdbmod.Function | None]]:
        """[(pc, function)] for the current frame plus callers found by scanning the stack for
        return addresses that follow a JSR/BSR (HCS08 has no frame pointer)."""
        with self._lock:
            r = self.target.regs()
            lo, hi = self.cdb.code_range()
            frames: list[tuple[int, cdbmod.Function | None]] = [(r.pc, self.cdb.function_at(r.pc))]
            sp = r.sp + 1
            top = min(sp + 96, 0x10000 - 2)
            data = self.target.read_mem(sp, top - sp) if top > sp else b""
        i = 0
        while i + 1 < len(data) and len(frames) < limit:
            ret = (data[i] << 8) | data[i + 1]
            if lo <= ret < hi and self._preceded_by_call(ret):
                frames.append((ret, self.cdb.function_at(ret)))
                i += 2
                continue
            i += 1
        return frames

    def _preceded_by_call(self, ret: int) -> bool:
        with self._lock:
            before = self.target.read_mem(ret - 3, 3)
        return before[0] in (0xCD, 0xDD) or before[1] in (0xAD, 0xBD, 0xED) or before[2] == 0xFD

    def req_stackTrace(self, args: dict) -> dict:
        frames = []
        for i, (pc, func) in enumerate(self._unwind()):
            rec = self.cdb.line_at(pc)
            frame: dict[str, Any] = {"id": i + 1, "name": func.name if func else f"0x{pc:04X}", "line": rec.line if rec else 0,
                                     "column": 1, "instructionPointerReference": f"0x{pc:04X}"}
            if rec is not None:
                frame["source"] = self._source(rec.file)
            if i > 0:
                frame["presentationHint"] = "subtle"
            frames.append(frame)
        return {"stackFrames": frames, "totalFrames": len(frames)}

    def _source(self, file: str) -> dict:
        path = self.sources.get(os.path.basename(file))
        return {"name": os.path.basename(file), "path": path} if path else {"name": file}

    def _index_sources(self, roots: list[str]) -> None:
        self.sources = {}
        for root in roots:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in ("build", ".git", "node_modules")]
                for fn in filenames:
                    if fn.endswith((".c", ".h")) and fn not in self.sources:
                        self.sources[fn] = os.path.join(dirpath, fn)

    # ------------------------------------------------------------------ variables
    def _ref(self, obj: Any) -> int:
        ref = self._next_ref
        self._next_ref += 1
        self._var_refs[ref] = obj
        return ref

    def req_scopes(self, args: dict) -> dict:
        frame_id = int(args.get("frameId", 1))
        frames = self._unwind()
        pc = frames[frame_id - 1][0] if 0 < frame_id <= len(frames) else frames[0][0]
        func = self.cdb.function_at(pc)
        scopes = [
            {"name": "局部变量", "presentationHint": "locals", "variablesReference": self._ref(("locals", func)), "expensive": False},
            {"name": "寄存器", "presentationHint": "registers", "variablesReference": self._ref(("registers", None)), "expensive": False},
            {"name": "全局变量", "variablesReference": self._ref(("globals", func)), "expensive": False},
            {"name": "外设寄存器", "variablesReference": self._ref(("sfr", None)), "expensive": True},
        ]
        return {"scopes": scopes}

    def req_variables(self, args: dict) -> dict:
        obj = self._var_refs.get(int(args.get("variablesReference", 0)))
        if obj is None:
            return {"variables": []}
        kind = obj[0]
        if kind == "locals":
            func = obj[1]
            syms = self.cdb.locals_of(func) if func else []
            return {"variables": [self._symbol_variable(s) for s in syms]}
        if kind == "registers":
            return {"variables": self._register_variables()}
        if kind == "globals":
            func = obj[1]
            syms = [s for s in self.cdb.globals_of(func.module if func else None) if not self._is_sfr(s)]
            return {"variables": [self._symbol_variable(s) for s in sorted(syms, key=lambda s: s.name)]}
        if kind == "sfr":
            syms = [s for s in self.cdb.globals_of(None) if self._is_sfr(s)]
            return {"variables": [self._symbol_variable(s) for s in sorted(syms, key=lambda s: s.address or 0)]}
        if kind == "struct":
            _, addr, t, module = obj
            return {"variables": [self._member_variable(addr, m, module) for m in self.cdb.struct_members(t.struct_name, module)]}
        if kind == "array":
            _, addr, t, module = obj
            et = t.element()
            n = min(t.array_length, 256)
            return {"variables": [self._typed_variable(f"[{i}]", addr + i * et.size, et, module) for i in range(n)]}
        if kind == "pointer":
            _, addr, t, module = obj
            return {"variables": [self._typed_variable("*", addr, t.element(), module)]}
        return {"variables": []}

    @staticmethod
    def _is_sfr(s: cdbmod.Symbol) -> bool:
        return s.name.startswith("_") and s.type.is_struct and s.type.struct_name.startswith("__") and (s.address or 0) < 0x1900

    def _register_variables(self) -> list[dict]:
        with self._lock:
            r = self.target.regs()
        flags = "".join(n if r.ccr & m else "-" for n, m in (("V", 0x80), ("H", 0x10), ("I", 0x08), ("N", 0x04), ("Z", 0x02), ("C", 0x01)))
        return [
            {"name": "PC", "value": f"0x{r.pc:04X}", "variablesReference": 0, "memoryReference": f"0x{r.pc:04X}"},
            {"name": "SP", "value": f"0x{r.sp:04X}", "variablesReference": 0, "memoryReference": f"0x{r.sp:04X}"},
            {"name": "HX", "value": f"0x{r.hx:04X}", "variablesReference": 0},
            {"name": "A", "value": f"0x{r.a:02X} ({r.a})", "variablesReference": 0},
            {"name": "CCR", "value": f"0x{r.ccr:02X} {flags}", "variablesReference": 0},
        ]

    def _symbol_variable(self, s: cdbmod.Symbol) -> dict:
        if s.address is None:
            where = ",".join(s.registers).upper() if s.registers else "寄存器/已优化"
            return {"name": s.name, "value": f"<{where}>", "type": s.type.c_name(), "variablesReference": 0}
        return self._typed_variable(s.name, s.address, s.type, s.module)

    def _member_variable(self, base: int, m: cdbmod.StructMember, module: str) -> dict:
        return self._typed_variable(m.name, base + m.offset, m.type, module)

    def _typed_variable(self, name: str, addr: int, t: cdbmod.TypeInfo, module: str) -> dict:
        var: dict[str, Any] = {"name": name, "type": t.c_name(), "variablesReference": 0, "memoryReference": f"0x{addr:04X}",
                               "evaluateName": name}
        if t.is_struct:
            var["value"] = f"{{...}} @0x{addr:04X}"
            var["variablesReference"] = self._ref(("struct", addr, t, module))
        elif t.is_array:
            et = t.element()
            with self._lock:
                data = self.target.read_mem(addr, min(t.size, 64))
            if et.base == "SC" and et.size == 1:
                text = data.split(b"\0", 1)[0].decode("ascii", errors="replace")
                var["value"] = f'"{text}" [{t.array_length}] @0x{addr:04X}'
            else:
                var["value"] = f"[{t.array_length}] @0x{addr:04X}"
            var["variablesReference"] = self._ref(("array", addr, t, module))
            var["indexedVariables"] = min(t.array_length, 256)
        elif t.is_pointer:
            with self._lock:
                p = int.from_bytes(self.target.read_mem(addr, t.size), ENDIAN) if t.size <= 2 else int.from_bytes(self.target.read_mem(addr, 2), ENDIAN)
            var["value"] = f"0x{p:04X}"
            if p:
                var["variablesReference"] = self._ref(("pointer", p, t, module))
        else:
            var["value"] = self._format_scalar(addr, t)
        return var

    def _read_scalar(self, addr: int, t: cdbmod.TypeInfo) -> int:
        with self._lock:
            data = self.target.read_mem(addr, max(t.size, 1))
        if t.is_bitfield:
            off, width = t.bitfield
            return (data[0] >> off) & ((1 << width) - 1)
        return int.from_bytes(data, ENDIAN, signed=t.signed)

    def _format_scalar(self, addr: int, t: cdbmod.TypeInfo) -> str:
        v = self._read_scalar(addr, t)
        if t.is_bitfield:
            return str(v)
        if t.base == "SF":
            import struct
            with self._lock:
                data = self.target.read_mem(addr, 4)
            return repr(struct.unpack(">f" if ENDIAN == "big" else "<f", data)[0])
        width = t.size * 2
        if t.signed:
            return f"{v} (0x{v & ((1 << (8 * t.size)) - 1):0{width}X})"
        if t.base == "SC" and 32 <= v < 127:
            return f"{v} '{chr(v)}' (0x{v:02X})"
        return f"{v} (0x{v:0{width}X})"

    def req_setVariable(self, args: dict) -> dict:
        obj = self._var_refs.get(int(args.get("variablesReference", 0)))
        name, text = args.get("name", ""), str(args.get("value", "")).strip()
        value = int(text, 0)
        if obj and obj[0] == "registers":
            with self._lock:
                self.target.write_reg(name.lower(), value)
            return {"value": f"0x{value:04X}"}
        addr, t = self._locate(obj, name)
        if addr is None:
            raise DapError(f"{name} 没有内存地址，无法修改")
        with self._lock:
            if t.is_bitfield:
                off, width = t.bitfield
                old = self.target.read_mem(addr, 1)[0]
                mask = ((1 << width) - 1) << off
                self.target.write_mem(addr, bytes([(old & ~mask) | ((value << off) & mask)]))
            else:
                self.target.write_mem(addr, (value & ((1 << (8 * t.size)) - 1)).to_bytes(t.size, ENDIAN))
        return {"value": self._format_scalar(addr, t)}

    def _locate(self, obj: Any, name: str) -> tuple[int | None, cdbmod.TypeInfo | None]:
        if obj is None:
            return None, None
        kind = obj[0]
        if kind in ("locals", "globals", "sfr"):
            func = obj[1] if kind != "sfr" else None
            for s in (self.cdb.locals_of(func) if kind == "locals" and func else self.cdb.globals_of(None)):
                if s.name == name:
                    return s.address, s.type
        elif kind == "struct":
            _, addr, t, module = obj
            for m in self.cdb.struct_members(t.struct_name, module):
                if m.name == name:
                    return addr + m.offset, m.type
        elif kind == "array":
            _, addr, t, module = obj
            et = t.element()
            return addr + int(name.strip("[]")) * et.size, et
        elif kind == "pointer":
            _, addr, t, module = obj
            return addr, t.element()
        return None, None

    # ------------------------------------------------------------------ evaluate / memory
    def req_evaluate(self, args: dict) -> dict:
        expr = str(args.get("expression", "")).strip()
        pc = self._pc()
        func = self.cdb.function_at(pc)
        if expr.startswith("*"):
            addr = int(expr[1:].strip(), 0)
            with self._lock:
                b = self.target.read_mem(addr, 1)[0]
            return {"result": f"{b} (0x{b:02X})", "variablesReference": 0, "memoryReference": f"0x{addr:04X}"}
        if expr.startswith("&"):
            s = self.cdb.find_symbol(expr[1:].strip(), func)
            if s is None or s.address is None:
                raise DapError(f"未知符号 {expr[1:]}")
            return {"result": f"0x{s.address:04X}", "variablesReference": 0, "memoryReference": f"0x{s.address:04X}"}
        name, _, member = expr.partition(".")
        s = self.cdb.find_symbol(name, func)
        if s is None:
            try:
                v = int(expr, 0)
                return {"result": f"{v} (0x{v:X})", "variablesReference": 0}
            except ValueError:
                raise DapError(f"未知符号 {name}") from None
        var = self._symbol_variable(s)
        if member and s.type.is_struct and s.address is not None:
            for m in self.cdb.struct_members(s.type.struct_name, s.module):
                if m.name == member:
                    var = self._member_variable(s.address, m, s.module)
                    break
            else:
                raise DapError(f"{name} 没有成员 {member}")
        return {"result": var["value"], "type": var.get("type"), "variablesReference": var["variablesReference"],
                "memoryReference": var.get("memoryReference")}

    def req_readMemory(self, args: dict) -> dict:
        addr = int(args["memoryReference"], 0) + int(args.get("offset", 0))
        count = int(args.get("count", 0))
        with self._lock:
            data = self.target.read_mem(addr, count) if count else b""
        return {"address": f"0x{addr:04X}", "data": base64.b64encode(data).decode()}

    def req_writeMemory(self, args: dict) -> dict:
        addr = int(args["memoryReference"], 0) + int(args.get("offset", 0))
        data = base64.b64decode(args.get("data", ""))
        with self._lock:
            self.target.write_mem(addr, data)
        return {"bytesWritten": len(data)}

    def req_source(self, args: dict) -> dict:
        raise DapError("源码只能从文件读取")


# ============================================================================ stdio transport


class StdioServer:
    def __init__(self, adapter_factory: Callable[[Callable[[dict], None]], Adapter], log=None):
        self._log = log or (lambda s: None)
        self._out = sys.stdout.buffer
        self._out_lock = threading.Lock()
        self.adapter = adapter_factory(self.send)

    def send(self, msg: dict) -> None:
        body = json.dumps(msg).encode("utf-8")
        with self._out_lock:
            self._out.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
            self._out.flush()
        self._log("-> " + body.decode("utf-8", "replace")[:400])

    def serve(self) -> None:
        stdin = sys.stdin.buffer
        while not self.adapter.exited.is_set():
            headers: dict[str, str] = {}
            while True:
                line = stdin.readline()
                if not line:
                    return
                line = line.decode("ascii", "replace").strip()
                if not line:
                    break
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
            length = int(headers.get("content-length", "0"))
            body = stdin.read(length)
            if not body:
                return
            self._log("<- " + body.decode("utf-8", "replace")[:400])
            try:
                msg = json.loads(body)
            except json.JSONDecodeError:
                continue
            self.adapter.handle_message(msg)

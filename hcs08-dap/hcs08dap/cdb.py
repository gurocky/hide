"""Parser for the SDCC ``.cdb`` debug file (``sdcc --debug``).

Only what a source-level debugger needs: modules, functions with address ranges,
C line <-> address records, symbols (globals, file statics, locals) with address /
register / type, and struct layouts.  Format reference: SDCC manual, "cdb file format".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Iterable

# ----------------------------------------------------------------------------- types


@dataclass(frozen=True)
class TypeInfo:
    """A decoded SDCC type chain, e.g. ``{64}DA64d,SC:U`` -> size 64, ['DA64d', 'SC'], unsigned."""

    size: int
    chain: tuple[str, ...]
    signed: bool

    @property
    def base(self) -> str:
        return self.chain[-1] if self.chain else "SV"

    @property
    def is_function(self) -> bool:
        return bool(self.chain) and self.chain[0] == "DF"

    @property
    def is_array(self) -> bool:
        return bool(self.chain) and self.chain[0].startswith("DA")

    @property
    def is_pointer(self) -> bool:
        return bool(self.chain) and self.chain[0] in ("DG", "DC", "DX", "DD", "DI", "DP")

    @property
    def is_struct(self) -> bool:
        return len(self.chain) == 1 and self.chain[0].startswith("ST")

    @property
    def is_bitfield(self) -> bool:
        return len(self.chain) == 1 and self.chain[0].startswith("SB")

    @property
    def struct_name(self) -> str | None:
        return self.chain[0][2:] if self.is_struct else None

    @property
    def array_length(self) -> int:
        m = re.match(r"DA(\d+)", self.chain[0]) if self.is_array else None
        return int(m.group(1)) if m else 0

    @property
    def bitfield(self) -> tuple[int, int]:
        """(bit offset, bit width) for ``SB<off>$<width>``."""
        m = re.match(r"SB(\d+)\$(\d+)", self.chain[0])
        return (int(m.group(1)), int(m.group(2))) if m else (0, 8)

    def element(self) -> "TypeInfo":
        """Type of one array element / the pointee of a pointer."""
        rest = self.chain[1:]
        if self.is_array and self.array_length:
            return TypeInfo(self.size // self.array_length, rest, self.signed)
        # pointer: size of the pointee is not recorded; derive from the base type
        return TypeInfo(_scalar_size(rest[-1]) if rest else 1, rest, self.signed)

    def c_name(self) -> str:
        names = {"SC": "char", "SS": "short", "SI": "int", "SL": "long", "SF": "float", "SV": "void", "SX": "sbit"}
        base = self.base
        if base.startswith("ST"):
            s = "struct " + base[2:]
        elif base.startswith("SB"):
            off, width = self.bitfield
            s = f"unsigned:{width}"
        else:
            s = names.get(base, base)
            if base in ("SC", "SS", "SI", "SL") and not self.signed:
                s = "unsigned " + s
        for d in reversed(self.chain[:-1]):
            if d.startswith("DA"):
                s += f"[{re.match(r'DA(\d+)', d).group(1)}]"
            elif d == "DF":
                s += "()"
            else:
                s += " *"
        return s


def _scalar_size(base: str) -> int:
    return {"SC": 1, "SS": 2, "SI": 2, "SL": 4, "SF": 4, "SV": 1, "SX": 1}.get(base[:2], 1)


def parse_type(text: str) -> TypeInfo:
    """``{64}DA64d,SC:U`` -> TypeInfo."""
    m = re.match(r"\{(\d+)\}(.*)$", text)
    if not m:
        raise ValueError(f"bad type record: {text!r}")
    size = int(m.group(1))
    parts = m.group(2).split(",")
    signed = True
    last = parts[-1]
    if ":" in last:
        last, sign = last.rsplit(":", 1)
        signed = sign != "U"
        parts[-1] = last
    return TypeInfo(size, tuple(parts), signed)


# ----------------------------------------------------------------------------- records


@dataclass
class Symbol:
    name: str
    scope: str            # 'G' global, 'F' file static, 'L' local
    module: str           # defining module ('' when unknown)
    function: str | None  # for locals: the function name
    type: TypeInfo
    space: str            # A..Z address space code ('R' register)
    registers: tuple[str, ...] = ()
    address: int | None = None
    key: str = ""         # raw key as in the file, used to join the L: record

    @property
    def is_function(self) -> bool:
        return self.type.is_function


@dataclass
class Function:
    name: str
    module: str
    scope: str
    start: int | None = None
    end: int | None = None      # exclusive (address of the 'X' record)
    is_interrupt: bool = False

    def contains(self, addr: int) -> bool:
        return self.start is not None and self.end is not None and self.start <= addr < self.end


@dataclass
class StructMember:
    name: str
    offset: int
    type: TypeInfo


@dataclass
class LineRecord:
    file: str
    line: int
    address: int


@dataclass
class Cdb:
    path: str = ""
    modules: list[str] = field(default_factory=list)
    functions: list[Function] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    lines: list[LineRecord] = field(default_factory=list)
    structs: dict[tuple[str, str], list[StructMember]] = field(default_factory=dict)

    # ---- derived indexes (built by finish())
    _line_starts: set[int] = field(default_factory=set)
    _addr_to_line: dict[int, LineRecord] = field(default_factory=dict)
    _sorted_line_addrs: list[int] = field(default_factory=list)
    _file_lines: dict[str, dict[int, list[int]]] = field(default_factory=dict)

    # ------------------------------------------------------------------ queries
    def function_at(self, addr: int) -> Function | None:
        for f in self.functions:
            if f.contains(addr):
                return f
        return None

    def function_named(self, name: str, module: str | None = None) -> Function | None:
        for f in self.functions:
            if f.name == name and (module is None or f.module == module):
                return f
        return None

    def line_at(self, addr: int) -> LineRecord | None:
        """The C line whose code contains ``addr`` (greatest line-start address <= addr, same function)."""
        if addr in self._addr_to_line:
            return self._addr_to_line[addr]
        import bisect
        i = bisect.bisect_right(self._sorted_line_addrs, addr) - 1
        if i < 0:
            return None
        rec = self._addr_to_line[self._sorted_line_addrs[i]]
        f = self.function_at(addr)
        if f is not None and not f.contains(rec.address):
            return None
        return rec

    def is_line_start(self, addr: int) -> bool:
        return addr in self._line_starts

    def files(self) -> Iterable[str]:
        return self._file_lines.keys()

    def address_for_line(self, file: str, line: int) -> tuple[int, int] | None:
        """(address, actual line) for a breakpoint request; moves down to the next line with code."""
        table = self._file_lines.get(os.path.basename(file))
        if not table:
            return None
        for ln in sorted(table):
            if ln >= line:
                return min(table[ln]), ln
        return None

    def locals_of(self, func: Function) -> list[Symbol]:
        return [s for s in self.symbols if s.scope == "L" and s.module == func.module and s.function == func.name and not s.is_function]

    def globals_of(self, module: str | None = None) -> list[Symbol]:
        out = []
        for s in self.symbols:
            if s.is_function or s.address is None:
                continue
            if s.scope == "G" or (s.scope == "F" and (module is None or s.module == module)):
                out.append(s)
        return out

    def struct_members(self, name: str, module: str) -> list[StructMember]:
        if (module, name) in self.structs:
            return self.structs[(module, name)]
        for (_m, n), members in self.structs.items():
            if n == name:
                return members
        return []

    def find_symbol(self, name: str, func: Function | None) -> Symbol | None:
        if func is not None:
            for s in self.locals_of(func):
                if s.name == name:
                    return s
        for s in self.symbols:
            if s.name == name and s.scope == "G" and not s.is_function:
                return s
        for s in self.symbols:
            if s.name == name and s.scope == "F" and not s.is_function and (func is None or s.module == func.module):
                return s
        return None

    def code_range(self) -> tuple[int, int]:
        starts = [f.start for f in self.functions if f.start is not None]
        ends = [f.end for f in self.functions if f.end is not None]
        return (min(starts), max(ends)) if starts and ends else (0, 0)

    # ------------------------------------------------------------------ build
    def finish(self) -> None:
        self._addr_to_line = {}
        self._file_lines = {}
        for rec in self.lines:
            # several records can share an address (e.g. 'for' header); keep the first
            self._addr_to_line.setdefault(rec.address, rec)
            self._file_lines.setdefault(rec.file, {}).setdefault(rec.line, []).append(rec.address)
        self._line_starts = set(self._addr_to_line)
        self._sorted_line_addrs = sorted(self._addr_to_line)


_SYM_RE = re.compile(r"^(?P<key>[^(]+)\((?P<type>[^)]*)\),(?P<space>[A-Z]),(?P<onstack>-?\d+),(?P<stackoff>-?\d+)(?:,\[(?P<regs>[^\]]*)\])?")
_FUNC_RE = re.compile(r"^(?P<key>[^(]+)\((?P<type>[^)]*)\),(?P<space>[A-Z]),(?P<onstack>-?\d+),(?P<stackoff>-?\d+),(?P<isr>\d+),(?P<intno>\d+),(?P<bank>\d+)")
_MEMBER_RE = re.compile(r"\(\{(\d+)\}S:S\$([^$]+)\$[^(]*\(([^)]*)\),([A-Z]),-?\d+,-?\d+\)")


def _split_key(key: str) -> tuple[str, str, str | None, str]:
    """``G$name$lvl$blk`` / ``Fmodule$name$lvl$blk`` / ``Lmodule.func$name$lvl$blk``
    -> (scope, module, function, name)."""
    scope = key[0]
    rest = key[1:]
    parts = rest.split("$")
    if scope == "G":
        return "G", "", None, parts[1]
    if scope == "F":
        return "F", parts[0], None, parts[1]
    if scope == "L":
        mod, _, func = parts[0].partition(".")
        return "L", mod, func, parts[1]
    return scope, "", None, parts[-3] if len(parts) >= 3 else rest


def _norm_key(scope: str, module: str, function: str | None, name: str, lvl_blk: str) -> str:
    """Join key for S:/F: records and their L: address records (function records drop the level)."""
    if scope == "L":
        return f"L{module}.{function}${name}${lvl_blk}"
    return f"{scope}{module}${name}"


def parse(path: str) -> Cdb:
    cdb = Cdb(path=path)
    addresses: dict[str, int] = {}
    ends: dict[str, int] = {}
    module = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")
            if len(line) < 2 or line[1] != ":":
                continue
            kind, body = line[0], line[2:]
            if kind == "M":
                module = body.strip()
                cdb.modules.append(module)
            elif kind == "F":
                m = _FUNC_RE.match(body)
                if not m:
                    continue
                scope, mod, func, name = _split_key(m.group("key"))
                mod = mod or module
                cdb.functions.append(Function(name=name, module=mod, scope=scope, is_interrupt=m.group("isr") != "0"))
            elif kind == "S":
                m = _SYM_RE.match(body)
                if not m:
                    continue
                scope, mod, func, name = _split_key(m.group("key"))
                lvl_blk = "$".join(m.group("key").split("$")[-2:])
                regs = tuple(r for r in (m.group("regs") or "").split(",") if r)
                sym = Symbol(name=name, scope=scope, module=mod or module, function=func, type=parse_type(m.group("type")),
                             space=m.group("space"), registers=regs, key=_norm_key(scope, mod, func, name, lvl_blk))
                cdb.symbols.append(sym)
            elif kind == "L":
                key, _, hexaddr = body.rpartition(":")
                try:
                    addr = int(hexaddr, 16)
                except ValueError:
                    continue
                if key.startswith("C$"):
                    parts = key[2:].split("$")
                    if len(parts) >= 2:
                        cdb.lines.append(LineRecord(file=parts[0], line=int(parts[1]), address=addr))
                elif key.startswith("A$"):
                    continue
                elif key.startswith("X"):
                    scope, mod, func, name = _split_key(key[1:])
                    ends[_norm_key(scope, mod, func, name, "")] = addr
                else:
                    scope, mod, func, name = _split_key(key)
                    lvl_blk = "$".join(key.split("$")[-2:])
                    addresses[_norm_key(scope, mod, func, name, lvl_blk)] = addr
            elif kind == "T":
                m = re.match(r"^F([^$]+)\$([^\[]+)\[(.*)\]$", body)
                if not m:
                    continue
                members = [StructMember(name=mm.group(2), offset=int(mm.group(1)), type=parse_type(mm.group(3)))
                           for mm in _MEMBER_RE.finditer(m.group(3))]
                cdb.structs[(m.group(1), m.group(2))] = members
    for s in cdb.symbols:
        s.address = addresses.get(s.key)
    for fn in cdb.functions:
        k = _norm_key(fn.scope, fn.module if fn.scope == "F" else "", None, fn.name, "")
        fn.start = addresses.get(k)
        fn.end = ends.get(k)
    # a function's end can be missing; fall back to the next function start
    known = sorted((f.start, f) for f in cdb.functions if f.start is not None)
    for i, (start, fn) in enumerate(known):
        if fn.end is None:
            fn.end = known[i + 1][0] if i + 1 < len(known) else start + 1
    cdb.finish()
    return cdb

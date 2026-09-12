"""Locates the firmware build used as the test fixture.

The tests parse a real SDCC ``--debug`` build of the CAL32 firmware (cal32-fw). Set
``HCS08_DAP_FIXTURE`` to that project's directory, or keep ``cal32-fw`` next to ``HIDE``
(or, for the old layout, next to ``hcs08-dap``). Tests are skipped when it is absent.
"""
import os
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.environ.get("HCS08_DAP_FIXTURE") or "",
    os.path.join(_HERE, "..", "..", "..", "cal32-fw"),   # sibling of HIDE
    os.path.join(_HERE, "..", "..", "cal32-fw"),         # sibling of hcs08-dap
]


def firmware_dir() -> str:
    for c in _CANDIDATES:
        if c and os.path.isfile(os.path.join(c, "build", "cal32.cdb")):
            return os.path.normpath(c)
    raise unittest.SkipTest("cal32-fw build (build/cal32.cdb) not found; set HCS08_DAP_FIXTURE")

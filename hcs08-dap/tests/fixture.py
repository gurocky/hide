"""The firmware build the tests parse: tests/fixtures/sample, a small SDCC S08 program whose
build/ outputs (sample.s19, sample.cdb) are committed. See its Makefile before rebuilding it:
the tests assert addresses and line numbers of that exact build.
"""
import os

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sample")


def firmware_dir() -> str:
    return SAMPLE

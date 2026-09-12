#!/usr/bin/env python3
"""Launch the HCS08 debug adapter over stdio (VSCode runs this; --log writes a trace file)."""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hcs08dap.adapter import Adapter, StdioServer  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", help="write a protocol / error trace to this file")
    ns = ap.parse_args()
    log = (lambda s: None)
    if ns.log:
        fh = open(ns.log, "a", buffering=1)
        log = lambda s: fh.write(s.rstrip("\n") + "\n")  # noqa: E731
    server = StdioServer(lambda send: Adapter(send, log=log), log=log)
    server.serve()


if __name__ == "__main__":
    main()

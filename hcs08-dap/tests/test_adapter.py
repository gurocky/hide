import os
import queue
import unittest

from hcs08dap.adapter import Adapter
from hcs08dap.target import FakeTarget

from fixture import firmware_dir


class Session:
    """Drives an Adapter in-process: requests are synchronous, events are queued."""

    def __init__(self):
        self.responses = {}
        self.events = queue.Queue()
        self.fake = None
        self.adapter = Adapter(self._recv, target_factory=self._factory)
        self.seq = 0

    def _factory(self, args):
        self.fake = FakeTarget(addresses=[], reset_pc=0)
        return self.fake

    def _recv(self, msg):
        if msg["type"] == "response":
            self.responses[msg["request_seq"]] = msg
        else:
            self.events.put(msg)

    def request(self, command, **arguments):
        self.seq += 1
        self.adapter.handle_message({"seq": self.seq, "type": "request", "command": command, "arguments": arguments})
        resp = self.responses.pop(self.seq)
        if not resp["success"]:
            raise AssertionError(f"{command} failed: {resp.get('message')}")
        return resp.get("body")

    def wait_event(self, name, timeout=5):
        while True:
            ev = self.events.get(timeout=timeout)
            if ev["event"] == name:
                return ev["body"]


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global SRC
        SRC = os.path.join(firmware_dir(), "src")

    def start(self, **extra):
        s = Session()
        caps = s.request("initialize", adapterID="hcs08-usbdm")
        self.assertTrue(caps["supportsConfigurationDoneRequest"])
        s.request("launch", program=os.path.join(SRC, "..", "build", "cal32.s19"), fake=True, flash=False, sourceRoots=[SRC], **extra)
        s.wait_event("initialized")
        return s

    def test_launch_runs_to_main_and_reports_stack(self):
        s = self.start()
        s.request("configurationDone")
        body = s.wait_event("stopped")
        self.assertEqual(body["reason"], "entry")
        frames = s.request("stackTrace", threadId=1)["stackFrames"]
        self.assertEqual(frames[0]["name"], "main")
        self.assertEqual(frames[0]["source"]["name"], "main.c")
        self.assertTrue(frames[0]["source"]["path"].endswith("main.c"))
        self.assertEqual(frames[0]["line"], 14)

    def test_breakpoint_step_variables_evaluate(self):
        s = self.start()
        bps = s.request("setBreakpoints", source={"path": os.path.join(SRC, "crc8.c")}, breakpoints=[{"line": 8}, {"line": 4}])["breakpoints"]
        self.assertTrue(bps[0]["verified"])
        self.assertEqual(bps[0]["line"], 8)
        self.assertEqual(bps[1]["line"], 5)                       # moved down to the first line with code
        s.request("configurationDone")
        s.wait_event("stopped")                                    # entry at main
        s.request("continue", threadId=1)
        body = s.wait_event("stopped")
        self.assertEqual(body["reason"], "breakpoint")
        frames = s.request("stackTrace", threadId=1)["stackFrames"]
        self.assertEqual(frames[0]["name"], "crc8")
        self.assertEqual(frames[0]["line"], 5)                    # lowest breakpoint address reached first
        # locals: len @0x156, data @0x157 (pointer), crc @0x159
        s.fake.write_mem(0x156, bytes([7]))
        s.fake.write_mem(0x157, (0x0123).to_bytes(2, "big"))
        s.fake.write_mem(0x123, b"\xA5")
        scopes = s.request("scopes", frameId=1)["scopes"]
        locals_ref = next(sc["variablesReference"] for sc in scopes if sc["name"] == "局部变量")
        variables = {v["name"]: v for v in s.request("variables", variablesReference=locals_ref)["variables"]}
        self.assertTrue(variables["len"]["value"].startswith("7 "))
        self.assertEqual(variables["data"]["value"], "0x0123")
        deref = s.request("variables", variablesReference=variables["data"]["variablesReference"])["variables"]
        self.assertTrue(deref[0]["value"].startswith("165 "))
        self.assertIn("<", variables["i"]["value"])               # register local
        # registers scope
        regs_ref = next(sc["variablesReference"] for sc in scopes if sc["name"] == "寄存器")
        regs = {v["name"]: v["value"] for v in s.request("variables", variablesReference=regs_ref)["variables"]}
        self.assertEqual(regs["PC"], "0x2758")
        # evaluate
        self.assertTrue(s.request("evaluate", expression="len", frameId=1)["result"].startswith("7 "))
        self.assertEqual(s.request("evaluate", expression="&len")["result"], "0x0156")
        self.assertTrue(s.request("evaluate", expression="*0x123")["result"].startswith("165"))
        # setVariable
        s.request("setVariable", variablesReference=locals_ref, name="len", value="0x20")
        self.assertEqual(s.fake.read_mem(0x156, 1), b"\x20")
        # step to the next line
        s.request("next", threadId=1)
        body = s.wait_event("stopped")
        self.assertEqual(body["reason"], "step")
        frames = s.request("stackTrace", threadId=1)["stackFrames"]
        self.assertEqual((frames[0]["name"], frames[0]["line"]), ("crc8", 8))
        s.request("stepIn", threadId=1)
        s.wait_event("stopped")
        self.assertEqual(s.request("stackTrace", threadId=1)["stackFrames"][0]["line"], 9)
        # struct member access through evaluate and the SFR scope (variable references are
        # invalidated at every stop, so ask for the scopes again like a real client does)
        scopes = s.request("scopes", frameId=1)["scopes"]
        sfr_ref = next(sc["variablesReference"] for sc in scopes if sc["name"] == "外设寄存器")
        sfr = {v["name"]: v for v in s.request("variables", variablesReference=sfr_ref)["variables"]}
        s.fake.write_mem(0x0000, b"\x81")
        members = {v["name"]: v for v in s.request("variables", variablesReference=sfr["_PTAD"]["variablesReference"])["variables"]}
        self.assertTrue(members["Byte"]["value"].startswith("129 "))
        bits = {v["name"]: v["value"] for v in s.request("variables", variablesReference=members["Bits"]["variablesReference"])["variables"]}
        self.assertEqual(bits["PTAD0"], "1")
        self.assertEqual(bits["PTAD7"], "1")
        self.assertEqual(bits["PTAD1"], "0")
        self.assertTrue(s.request("evaluate", expression="_PTAD.Byte")["result"].startswith("129 "))
        # memory read
        mem = s.request("readMemory", memoryReference="0x0156", count=1)
        self.assertEqual(mem["data"], "IA==")                      # base64 of 0x20
        s.request("disconnect")
        self.assertIn("close", s.fake.log)

    def test_breakpoint_limit_and_pause(self):
        s = self.start()
        bps = s.request("setBreakpoints", source={"path": os.path.join(SRC, "crc8.c")},
                        breakpoints=[{"line": 5}, {"line": 8}, {"line": 9}, {"line": 10}])["breakpoints"]
        self.assertEqual([b["verified"] for b in bps], [True, True, True, False])
        s.request("configurationDone")
        s.wait_event("stopped")
        s.request("setBreakpoints", source={"path": os.path.join(SRC, "crc8.c")}, breakpoints=[])
        s.request("continue", threadId=1)     # no breakpoints: fake target runs forever
        s.request("pause", threadId=1)
        body = s.wait_event("stopped")
        self.assertEqual(body["reason"], "pause")

    def test_launch_without_cdb_fails_cleanly(self):
        s = Session()
        s.request("initialize")
        s.seq += 1
        s.adapter.handle_message({"seq": s.seq, "type": "request", "command": "launch", "arguments": {"program": "/nonexistent/x.s19", "fake": True}})
        resp = s.responses.pop(s.seq)
        self.assertFalse(resp["success"])
        self.assertIn(".cdb", resp["message"])


if __name__ == "__main__":
    unittest.main()

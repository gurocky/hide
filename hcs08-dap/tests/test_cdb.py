import os
import unittest

from hcs08dap import cdb as cdbmod

from fixture import firmware_dir


class CdbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cdb = cdbmod.parse(os.path.join(firmware_dir(), "build", "cal32.cdb"))

    def test_type_parsing(self):
        t = cdbmod.parse_type("{64}DA64d,SC:U")
        self.assertTrue(t.is_array)
        self.assertEqual(t.array_length, 64)
        self.assertEqual(t.element().size, 1)
        self.assertEqual(t.c_name(), "unsigned char[64]")
        p = cdbmod.parse_type("{2}DG,SC:U")
        self.assertTrue(p.is_pointer)
        self.assertEqual(p.c_name(), "unsigned char *")
        f = cdbmod.parse_type("{2}DF,SV:S")
        self.assertTrue(f.is_function)
        b = cdbmod.parse_type("{1}SB6$1:U")
        self.assertTrue(b.is_bitfield)
        self.assertEqual(b.bitfield, (6, 1))
        s = cdbmod.parse_type("{2}ST__00000052:S")
        self.assertEqual(s.struct_name, "__00000052")

    def test_functions_have_ranges(self):
        main = self.cdb.function_named("main")
        self.assertIsNotNone(main)
        self.assertEqual(main.start, 0x1886)
        self.assertEqual(main.end, 0x1899)
        crc8 = self.cdb.function_named("crc8")
        self.assertEqual(crc8.start, 0x2752)
        self.assertIs(self.cdb.function_at(0x2760), crc8)
        static = self.cdb.function_named("write_byte", module="ad7799")
        self.assertEqual(static.scope, "F")
        self.assertEqual(static.start, 0x18A5)
        self.assertEqual(static.end, 0x18EA)

    def test_lines(self):
        self.assertEqual(self.cdb.address_for_line("crc8.c", 8), (0x275C, 8))
        # a line without code moves to the next line that has code
        addr, line = self.cdb.address_for_line("/x/y/crc8.c", 4)
        self.assertEqual((addr, line), (0x2758, 5))
        rec = self.cdb.line_at(0x2760)
        self.assertEqual((rec.file, rec.line), ("crc8.c", 8))
        self.assertTrue(self.cdb.is_line_start(0x2765))
        self.assertFalse(self.cdb.is_line_start(0x2766))
        self.assertIsNone(self.cdb.address_for_line("nothere.c", 1))

    def test_symbols(self):
        crc8 = self.cdb.function_named("crc8")
        locals_ = {s.name: s for s in self.cdb.locals_of(crc8)}
        self.assertEqual(locals_["len"].address, 0x156)
        self.assertEqual(locals_["data"].address, 0x157)
        self.assertTrue(locals_["data"].type.is_pointer)
        self.assertIsNone(locals_["i"].address)          # register / optimised
        rx = self.cdb.find_symbol("proto_rx", None)
        self.assertIsNotNone(rx)
        self.assertTrue(rx.type.is_array)
        self.assertEqual(rx.type.array_length, 64)
        self.assertIsNotNone(rx.address)
        ptad = self.cdb.find_symbol("_PTAD", None)
        self.assertEqual(ptad.address, 0)
        members = self.cdb.struct_members(ptad.type.struct_name, ptad.module)
        self.assertTrue(any(m.name == "Byte" for m in members))
        self.assertTrue(any(m.name == "Bits" for m in members))

    def test_code_range(self):
        lo, hi = self.cdb.code_range()
        self.assertLess(lo, 0x1900)
        self.assertGreater(hi, 0x2700)


if __name__ == "__main__":
    unittest.main()

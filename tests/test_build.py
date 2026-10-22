import pathlib
import tempfile
import unittest

from akscope import build

# objdump -r -j .rodata.app_task_table task_list.o, ak-base-kit-stm32l151 57e480e (trimmed)
OBJDUMP_TABLE = """
build/task_list.o:     file format elf32-littlearm

RELOCATION RECORDS FOR [.rodata.app_task_table]:
OFFSET   TYPE              VALUE 
00000004 R_ARM_ABS32       task_timer_tick
0000000c R_ARM_ABS32       _Z11task_systemP8ak_msg_t
00000024 R_ARM_ABS32       _Z9task_lifeP8ak_msg_t
00000034 R_ARM_ABS32       _Z12task_rf24_ifP8ak_msg_t
"""


class TestRelocations(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(build.parse_relocations(OBJDUMP_TABLE), [
            "task_timer_tick", "_Z11task_systemP8ak_msg_t", "_Z9task_lifeP8ak_msg_t",
            "_Z12task_rf24_ifP8ak_msg_t"])

    def test_empty(self):
        self.assertEqual(build.parse_relocations(""), [])


class TestGenerate(unittest.TestCase):
    def test_wrappers(self):
        src = build.generate_auto(["_Z9task_lifeP8ak_msg_t", "task_c_handler"],
                                  "_Z20task_polling_consolev", "app_task_table")
        self.assertIn("void __wrap__Z9task_lifeP8ak_msg_t(ak_msg_t* msg) {", src)
        self.assertIn("\t__real__Z9task_lifeP8ak_msg_t(msg);", src)
        self.assertIn("void __wrap_task_c_handler(ak_msg_t* msg) {", src)
        self.assertIn("void __wrap__Z20task_polling_consolev(void) {", src)
        self.assertIn("\takt_trace_poll();", src)
        self.assertIn("void __wrap_task_create(task_t* task_tbl) {", src)
        # AK code style: tabs, no spaces for indent
        self.assertFalse([l for l in src.splitlines() if l.startswith("    ")])

    def test_no_polling(self):
        src = build.generate_auto([], None, "app_task_table")
        self.assertNotIn("akt_trace_poll", src)


class TestProject(unittest.TestCase):
    def test_find_application_dir(self):
        with tempfile.TemporaryDirectory() as d:
            app = pathlib.Path(d) / "game" / "application"
            (app / "sources" / "ak").mkdir(parents=True)
            (app / "Makefile").write_text("all:\n")
            self.assertEqual(build.find_application_dir(app.parent), app.resolve())
            self.assertEqual(build.find_application_dir(app), app.resolve())

    def test_not_a_project(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(build.BuildError):
                build.find_application_dir(d)

    def test_cache_is_outside_project(self):
        with tempfile.TemporaryDirectory() as d:
            app = pathlib.Path(d).resolve() / "application"
            self.assertNotIn(str(app), str(build.cache_dir(app)))


if __name__ == "__main__":
    unittest.main()

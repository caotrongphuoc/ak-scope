import pathlib
import tempfile
import unittest

from akscope.verify import static

# what `g++ -E` gives for a small AK project, line markers included; every rule has a case
TASK_LIST = '''# 1 "sources/app/task_list.cpp"
enum { TASK_TIMER_TICK_ID, AC_TASK_LIFE_ID, AC_TASK_GAME_ID, AC_TASK_ZERO_ID, AK_TASK_EOT_ID, };
enum { AC_TASK_POLLING_CONSOLE_ID, AK_TASK_POLLING_EOT_ID, };
const task_t app_task_table[] = {
    {TASK_TIMER_TICK_ID, (7), task_timer_tick},
    {AC_TASK_LIFE_ID, (6), task_life},
    {AC_TASK_GAME_ID, (4), game_handle},
    {AC_TASK_ZERO_ID, (0), task_zero},
    {AK_TASK_EOT_ID, (0), (pf_task)0}};
const task_polling_t app_task_polling_table[] = {
    {AC_TASK_POLLING_CONSOLE_ID, (0x01), task_polling_console},
    {AK_TASK_POLLING_EOT_ID, (0x00), (pf_task_polling)0}};
'''

APP_H = '''# 1 "sources/app/app.h"
enum {
 AC_LIFE_CHECK = (10),
 AC_LIFE_UNUSED,
};
enum {
 GAME_START = (10),
 GAME_TICK,
 GAME_LOW = (0),
};
'''

LIFE = APP_H + '''# 1 "sources/app/task_life.cpp"
uint32_t score;
static uint8_t only_life;
void task_life(ak_msg_t* msg) {
 switch (msg->sig) {
 case AC_LIFE_CHECK:
  only_life = 1;
  score = 0;
  sys_ctrl_delay_ms(5);
  task_post_pure_msg(AC_TASK_GAME_ID, GAME_START);
  task_post_pure_msg(AC_TASK_GAME_ID, GAME_LOW);
  task_post_common_msg(AC_TASK_GAME_ID, GAME_TICK, (uint8_t*)&score, 100);
  break;
 }
}
void task_helper(void) {
}
'''

GAME = APP_H + '''# 1 "sources/app/game.cpp"
extern uint32_t score;
static void game_draw(void) {
 while (!(((SPI_TypeDef *) (0x40013000))->SR & 0x02));
 GPIO_SetBits(((GPIO_TypeDef *) (0x40020000)), 0x0001);
 eeprom_write(0, 0, 4);
}
void game_handle(ak_msg_t* msg) {
 switch (msg->sig) {
 case GAME_START:
  score++;
  game_draw();
  break;
 case GAME_TICK:
  break;
 }
}
void task_zero(ak_msg_t* msg) {
}
'''

SYSTEM = '''# 1 "sources/platform/stm32l/system.c"
void reset_handler() { main(); }
void good_irq() {
 task_entry_interrupt();
 task_post_pure_msg(1, 10);
 task_exit_interrupt();
}
void bad_irq() {
 button_isr_cb();
}
void dyn_irq() {
 task_entry_interrupt();
 task_post_dynamic_msg(1, 10, 0, 4);
 task_exit_interrupt();
}
__attribute__((section(".isr_vector")))
void (* const isr_vector[])() = {
  ((void (*)())(uint32_t)&_estack),
  reset_handler,
  good_irq,
  bad_irq,
  dyn_irq,
};
'''

BSP = '''# 1 "sources/app/app_bsp.cpp"
void button_isr_cb(void) {
 timer_set(1, 10, 100, TIMER_ONE_SHOT);
}
void task_polling_console(void) {
}
'''


def project():
    return static.Project({
        "sources/app/task_list.cpp": TASK_LIST,
        "sources/app/task_life.cpp": LIFE,
        "sources/app/game.cpp": GAME,
        "sources/platform/stm32l/system.c": SYSTEM,
        "sources/app/app_bsp.cpp": BSP,
    })


def by_rule(findings):
    out = {}
    for f in findings:
        out.setdefault(f.rule, []).append(f)
    return out


class TestModel(unittest.TestCase):
    def test_tables(self):
        p = project()
        self.assertEqual(p.handlers, {"task_timer_tick": 0, "task_life": 1, "game_handle": 2, "task_zero": 3})
        self.assertEqual(p.polling, {"task_polling_console"})
        self.assertEqual(p.isrs, {"good_irq", "bad_irq", "dyn_irq"})

    def test_reach(self):
        p = project()
        self.assertEqual(p.reach(["game_handle"])["game_draw"], ["game_handle", "game_draw"])


class TestRules(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = project()
        findings = []
        for rule in static.RULES:
            findings += rule(p)
        cls.f = by_rule(findings)

    def lines(self, rule):
        return sorted((f.file, f.line) for f in self.f.get(rule, []))

    def test_s02_delay_and_busy_wait(self):
        msgs = {(f.severity, f.file, f.line) for f in self.f["S02"]}
        self.assertIn(("error", "sources/app/task_life.cpp", 8), msgs)
        self.assertIn(("warning", "sources/app/game.cpp", 3), msgs)
        busy = next(f for f in self.f["S02"] if "busy wait" in f.message)
        self.assertIn("game_handle -> game_draw", busy.message)
        store = next(f for f in self.f["S02"] if "eeprom_write" in f.message)
        self.assertEqual((store.severity, store.line), ("warning", 5))

    def test_s03_io(self):
        self.assertEqual(self.lines("S03"), [("sources/app/game.cpp", 3), ("sources/app/game.cpp", 4)])

    def test_s04_prefix(self):
        names = [f.message.split("(")[0] for f in self.f["S04"]]
        self.assertEqual(names, ["task_helper"])

    def test_s05_low_signal(self):
        self.assertEqual([f.message.split()[1] for f in self.f["S05"]], ["GAME_LOW"])

    def test_s06_priority(self):
        self.assertEqual(len(self.f["S06"]), 1)
        self.assertIn("AC_TASK_ZERO_ID", self.f["S06"][0].message)

    def test_s07_posted_not_handled(self):
        self.assertEqual(len(self.f["S07"]), 1)
        self.assertIn("GAME_LOW", self.f["S07"][0].message)
        self.assertEqual(self.f["S07"][0].line, 10)

    def test_s08_dead_signal(self):
        self.assertEqual([f.message.split()[1] for f in self.f["S08"]], ["AC_LIFE_UNUSED"])

    def test_s09_common_payload(self):
        self.assertEqual(self.lines("S09"), [("sources/app/task_life.cpp", 11)])

    def test_s10_shared_global(self):
        self.assertEqual(len(self.f["S10"]), 1)
        f = self.f["S10"][0]
        self.assertTrue(f.message.startswith("score is written by 2 tasks"))
        self.assertEqual((f.file, f.line), ("sources/app/task_life.cpp", 1))

    def test_s11_isr_bracket(self):
        self.assertEqual(len(self.f["S11"]), 1)
        self.assertIn("bad_irq() reaches timer_set()", self.f["S11"][0].message)

    def test_s12_dynamic_in_isr(self):
        self.assertEqual(len(self.f["S12"]), 1)
        self.assertIn("task_post_dynamic_msg", self.f["S12"][0].message)


# a handler with the base kit's debug macros expanded, as -E gives them with APP_DBG_SIG_EN
PRINTS = APP_H + '''# 1 "sources/app/task_print.cpp"
void task_print(ak_msg_t* msg) {
 switch (msg->sig) {
 case AC_LIFE_CHECK:
  xprintf("-SIG-> " "AC_LIFE_CHECK\\n");
  xprintf("[DBG] " "score %d\\n", 1);
  xprintf("%s\\n", "an answer of the shell");
  break;
 }
}
'''


def main_app(nonblock):
    call = "sys_ctrl_shell_sw_to_nonblock();" if nonblock else ""
    return '''# 1 "sources/app/app.cpp"
int main_app() {
 task_create((task_t*)app_task_table);
 %s
 return task_run();
}
''' % call


class TestDebugPrints(unittest.TestCase):
    def run_s13(self, nonblock):
        p = static.Project({"sources/app/task_list.cpp": TASK_LIST.replace("task_zero}", "task_print}"),
                            "sources/app/task_print.cpp": PRINTS, "sources/app/app.cpp": main_app(nonblock)})
        return static.s13_debug_prints(p)

    def test_blocking_console(self):
        f = self.run_s13(False)
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0].rule, f[0].file, f[0].line), ("S13", "sources/app/app.cpp", 0))
        # the shell answer is not counted
        self.assertIn("2 debug prints run inside task handlers (1 APP_DBG_SIG, 1 APP_DBG)", f[0].message)

    def test_interrupt_driven_console(self):
        self.assertEqual(self.run_s13(True), [])


SIZES = '''# 1 "sources/app/sizes.cpp"
struct big_t { uint8_t data[80]; };
struct small_t { uint8_t a; };
static struct { small_t view; } holder;
void sizes_handle(ak_msg_t* msg, int n) {
 big_t big;
 small_t small = {0};
 uint8_t raw[70];
 task_post_common_msg(1, 10, (uint8_t*)&big, sizeof(big));
 task_post_common_msg(1, 11, (uint8_t*)&small, sizeof(small));
 task_post_common_msg(1, 12, raw, sizeof(raw));
 task_post_common_msg(1, 13, (uint8_t*)&holder.view, sizeof(holder.view));
 task_post_common_msg(1, 14, raw, n + 2);
}
'''


class TestS09Sizeof(unittest.TestCase):
    def setUp(self):
        self.p = static.Project({"sources/app/task_list.cpp": TASK_LIST, "sources/app/sizes.cpp": SIZES})

    def test_size_expressions(self):
        got = [(ln, length, size) for _, _, ln, length, size in static.common_posts(self.p)]
        self.assertEqual(got, [
            (8, "sizeof(big)", "big_t"),
            (9, "sizeof(small)", "small_t"),
            (10, "sizeof(raw)", "uint8_t[70]"),
            (11, "sizeof(holder.view)", "holder.view"),
            (12, "n + 2", None),
        ])
        self.assertEqual(len(static.size_probes(self.p)["sources/app/sizes.cpp"]), 4)

    def test_sizes_from_the_compiler(self):
        # what Builder.probe_sizes() returns for those expressions
        self.p.common_sizes = {("sources/app/sizes.cpp", 8): 80, ("sources/app/sizes.cpp", 9): 1,
                               ("sources/app/sizes.cpp", 10): 70}
        f = static.s09_common_payload(self.p)
        self.assertEqual([(x.line, x.message.split(" (")[0]) for x in f],
                         [(8, "common message of 80 bytes"), (10, "common message of 70 bytes")])


class TestS01(unittest.TestCase):
    def test_compare_with_base(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for root in ("game", "base"):
                ak = d / root / "application" / "sources" / "ak"
                ak.mkdir(parents=True)
                (ak / "task.c").write_text("int a;\nint b;\n")
                (ak / "ak.cfg.mk").write_text("POOL = 8\n")
            (d / "game/application/sources/ak/task.c").write_text("int a;\nint c;\n")
            (d / "game/application/sources/ak/ak.cfg.mk").write_text("POOL = 16\n")
            (d / "game/application/sources/ak/extra.h").write_text("#define X 1\n")
            f = static.s01_framework_unchanged(d / "game", d / "base")
        got = sorted((x.file, x.line, x.severity) for x in f)
        self.assertEqual(got, [("application/sources/ak/extra.h", 0, "error"),
                               ("application/sources/ak/task.c", 2, "error")])

    def test_no_base(self):
        f = static.s01_framework_unchanged("/x", None)
        self.assertEqual(f[0].severity, "warning")


class TestBaseKitDiff(unittest.TestCase):
    def test_new_lines(self):
        with tempfile.TemporaryDirectory() as d:
            d = pathlib.Path(d)
            for root, text in (("game", "a\nb\nNEW\nc\n"), ("base", "a\nb\nc\n")):
                f = d / root / "application" / "sources" / "driver" / "x.c"
                f.parent.mkdir(parents=True)
                f.write_text(text)
            diff = static.BaseKitDiff(d / "game", d / "base")
            self.assertEqual(diff.new_lines("sources/driver/x.c"), {3})
            self.assertIsNone(diff.new_lines("sources/app/none.c"))


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import pathlib
import unittest

from akscope.cli import main
from akscope.core import symbols
from akscope.core.symbols import SymbolTable

SAMPLES = pathlib.Path(__file__).resolve().parent.parent / "samples"

# what `g++ -E` gives for a small AK app: macros expanded, line markers kept
TASK_LIST = """
# 1 "sources/app/task_list.cpp"
enum
{
 TASK_TIMER_TICK_ID,
 AC_TASK_LIFE_ID,
 AC_TASK_DISPLAY_ID,
 EM_GAME_MATCH_ID,
 AK_TASK_EOT_ID,
};
const task_t app_task_table[] = {
    {TASK_TIMER_TICK_ID, (7), task_timer_tick},
    {AC_TASK_LIFE_ID, (6), task_life},
    {AC_TASK_DISPLAY_ID, (4), task_display},
    {EM_GAME_MATCH_ID, (4), em_game_match_handle},
    {AK_TASK_EOT_ID, (0), (pf_task)0}};
"""

APP = """
# 1 "sources/app/app.h"
enum { AC_LIFE_SYSTEM_CHECK = (10), };
enum { AC_DISPLAY_RENDER_SCREEN = (0), AC_DISPLAY_INITIAL = (10), AC_DISPLAY_SHOW_LOGO, };
enum { EM_GAME_MATCH_INIT = (10) + 90, EM_GAME_MATCH_START, EM_GAME_MATCH_HOME = 0x70U };
enum { EM_GAME_GOAL_RESULT_GOAL, EM_GAME_GOAL_RESULT_SAVE };
void task_life(ak_msg_t* msg) {
	switch (msg->sig) {
	case AC_LIFE_SYSTEM_CHECK:
		xprintf("{ not a block }\\n");
		break;
	}
}
void task_display(ak_msg_t* m) {
	switch (m->sig) {
	case AC_DISPLAY_INITIAL: {
		switch (result) {
		case EM_GAME_GOAL_RESULT_SAVE:
			break;
		}
		timer_set(AC_TASK_DISPLAY_ID, AC_DISPLAY_SHOW_LOGO, 2000, TIMER_ONE_SHOT);
		task_post_pure_msg(EM_GAME_MATCH_ID, EM_GAME_MATCH_INIT);
	}
		break;
	}
}
void scr_mng_render(void) {
	timer_set(AC_TASK_DISPLAY_ID, AC_DISPLAY_RENDER_SCREEN, 3, TIMER_ONE_SHOT);
}
"""


class TestEval(unittest.TestCase):
    def test_expressions(self):
        names = {"A": 10}
        self.assertEqual(symbols.eval_int("(10)", names), 10)
        self.assertEqual(symbols.eval_int("A + 90", names), 100)
        self.assertEqual(symbols.eval_int("0x70U", names), 0x70)
        self.assertEqual(symbols.eval_int("(1 << 3) | 1", names), 9)
        self.assertEqual(symbols.eval_int("(uint8_t)20", names), 20)
        self.assertEqual(symbols.eval_int("100 / 3", names), 33)
        self.assertIsNone(symbols.eval_int("UNKNOWN + 1", names))
        self.assertIsNone(symbols.eval_int("sizeof(x)", names))

    def test_enums(self):
        v = symbols.parse_enums(APP)
        self.assertEqual(v["AC_DISPLAY_SHOW_LOGO"], 11)
        self.assertEqual(v["EM_GAME_MATCH_INIT"], 100)
        self.assertEqual(v["EM_GAME_MATCH_START"], 101)
        self.assertEqual(v["EM_GAME_MATCH_HOME"], 0x70)
        self.assertEqual(v["EM_GAME_GOAL_RESULT_SAVE"], 1)


class TestHandlers(unittest.TestCase):
    def test_cases_only_from_sig_switch(self):
        h = symbols.parse_handlers(symbols.strip_literals(APP))
        self.assertEqual(h["task_life"], ["AC_LIFE_SYSTEM_CHECK"])
        # the nested switch (result) is not a signal switch
        self.assertEqual(h["task_display"], ["AC_DISPLAY_INITIAL"])

    def test_posts(self):
        self.assertEqual(symbols.parse_posts(APP), [
            ("AC_TASK_DISPLAY_ID", "AC_DISPLAY_SHOW_LOGO"),
            ("EM_GAME_MATCH_ID", "EM_GAME_MATCH_INIT"),
            ("AC_TASK_DISPLAY_ID", "AC_DISPLAY_RENDER_SCREEN")])


class TestBuildSymbols(unittest.TestCase):
    def setUp(self):
        self.data = symbols.build_symbols(TASK_LIST, [APP], [0, 0, 0, 3])
        self.sym = SymbolTable(json.loads(json.dumps(self.data)))

    def test_tasks(self):
        self.assertEqual(self.data["tasks"][2], {"name": "AC_TASK_DISPLAY_ID",
                                                 "handler": "task_display", "pri": 4})
        self.assertNotIn(4, self.data["tasks"])  # EOT row
        self.assertEqual(self.sym.task(0xEE), "isr")
        self.assertEqual(self.sym.task(0xEF), "idle")
        self.assertEqual(self.sym.task(99), "99")

    def test_signals_per_task(self):
        # value 10 means a different signal in each task
        self.assertEqual(self.sym.signal(1, 10), "AC_LIFE_SYSTEM_CHECK")
        self.assertEqual(self.sym.signal(2, 10), "AC_DISPLAY_INITIAL")
        self.assertEqual(self.sym.signal(2, 11), "AC_DISPLAY_SHOW_LOGO")
        self.assertEqual(self.sym.signal(2, 0), "AC_DISPLAY_RENDER_SCREEN")
        self.assertEqual(self.sym.signal(3, 100), "EM_GAME_MATCH_INIT")

    def test_guess_is_marked(self):
        # 100 is unique among the signal names, but task 1 never uses it
        self.assertEqual(self.sym.signal(1, 100), "EM_GAME_MATCH_INIT?")
        # 10 is used by several tasks, no guess
        self.assertEqual(self.sym.signal(3, 10), "10")

    def test_app_ver(self):
        self.assertEqual(symbols.parse_app_ver("#define X 1\n#define APP_VER {0, 0, 0, 3}\n"),
                         [0, 0, 0, 3])
        self.assertIsNone(symbols.parse_app_ver("#define APP_VER {0, 1}\n"))


class TestDumpWithSymbols(unittest.TestCase):
    def test_names(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            ret = main(["dump", str(SAMPLES / "11m-game-boot-akt.bin"),
                        "--sym", str(SAMPLES / "11m-game-symbols.json")])
        self.assertEqual(ret, 0)
        text = out.getvalue()
        self.assertIn("POST         idle -> AC_TASK_UART_IF_ID  sig AC_UART_IF_INIT", text)
        self.assertIn("task AC_TASK_DISPLAY_ID  sig AC_DISPLAY_INITIAL  pure  exe 139.099 ms", text)
        self.assertIn("sig AC_DISPLAY_RENDER_SCREEN", text)


if __name__ == "__main__":
    unittest.main()

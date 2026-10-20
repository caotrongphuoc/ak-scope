import unittest

from akscope.core.model import Dispatch, Trace, msg_type_name


def make(task_id, sig, exe=0):
    return Dispatch(origin="console", line=0, task_id=task_id, msg_type=0x80,
                    ref_count=1, sig=sig, wait_ms=0, exe_ms=exe)


class TestTrace(unittest.TestCase):
    def setUp(self):
        self.trace = Trace(dispatches=[make(4, 10), make(3, 10, 5), make(4, 10), make(3, 11)])

    def test_task_ids(self):
        self.assertEqual(self.trace.task_ids(), [3, 4])

    def test_by_task_keeps_order(self):
        groups = self.trace.by_task()
        self.assertEqual(list(groups), [3, 4])
        self.assertEqual([d.sig for d in groups[3]], [10, 11])

    def test_by_signal_splits_tasks(self):
        # sig 10 of task 3 and sig 10 of task 4 are different signals
        groups = self.trace.by_signal()
        self.assertEqual(list(groups), [(3, 10), (3, 11), (4, 10)])
        self.assertEqual(len(groups[(4, 10)]), 2)


class TestMsgTypeName(unittest.TestCase):
    def test_names(self):
        self.assertEqual(msg_type_name(0x80), "pure")
        self.assertEqual(msg_type_name(0xC0), "common")
        self.assertEqual(msg_type_name(0x40), "dynamic")
        self.assertEqual(msg_type_name(0x00), "0x00")


if __name__ == "__main__":
    unittest.main()

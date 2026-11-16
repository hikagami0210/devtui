import io
import unittest
from unittest.mock import patch

from devtui.__main__ import main
from devtui.config import UserError
from devtui.mousecheck import RawReports, run_mouse_test


class MouseCheckTests(unittest.TestCase):
    def test_fragmented_sgr_reports_preserve_both_directions_and_phase(self):
        decoder = RawReports()
        self.assertEqual(decoder.feed(b"\x1b[<64;"), [])
        events = decoder.feed(b"20;10M\x1b[<65;20;10M\x1b[<65;20;10m")
        self.assertEqual([event[0] for event in events], ["UP", "DOWN", "DOWN"])
        self.assertIn("release", events[-1][2])
        self.assertEqual(decoder.pending, b"")

    def test_classic_mouse_and_cursor_keys_are_identified_separately(self):
        decoder = RawReports()
        self.assertEqual(decoder.feed(b"\x1b[M"), [])
        events = decoder.feed(bytes((96, 42, 42)) + b"\x1b[M" + bytes((97, 42, 42)) + b"\x1bOA\x1b[B")
        self.assertEqual([event[0] for event in events], ["UP", "DOWN", "ARROW_UP", "ARROW_DOWN"])
        self.assertEqual([event[1] for event in events], ["X10", "X10", "KEY", "KEY"])

    def test_ordinary_text_and_malformed_reports_are_discarded(self):
        decoder = RawReports()
        self.assertEqual(decoder.feed(b"private text\x1b[<broken\x1b[<" + b"9" * 100), [])
        self.assertEqual(decoder.pending, b"")
        self.assertEqual(decoder.feed(b"q"), [])
        self.assertTrue(decoder.done)

    def test_non_terminal_has_actionable_error(self):
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaisesRegex(UserError, "端末"):
                run_mouse_test("raw")

    def test_diagnostic_does_not_load_workspace_or_backend(self):
        with patch("devtui.mousecheck.run_mouse_test", return_value=0) as diagnostic, \
                patch("devtui.__main__.load") as load, patch("devtui.__main__.Mise") as backend:
            self.assertEqual(main(["--mouse-test"]), 0)
            diagnostic.assert_called_once_with("raw")
            load.assert_not_called()
            backend.assert_not_called()
        with patch("devtui.mousecheck.run_mouse_test", return_value=0) as diagnostic:
            self.assertEqual(main(["--mouse-test", "curses"]), 0)
            diagnostic.assert_called_once_with("curses")

    def test_workspace_option_is_rejected_before_diagnostic(self):
        with patch("sys.stderr", io.StringIO()), patch("devtui.mousecheck.run_mouse_test") as diagnostic:
            with self.assertRaises(SystemExit) as error:
                main(["--mouse-test", "--demo"])
            self.assertEqual(error.exception.code, 2)
            diagnostic.assert_not_called()


if __name__ == "__main__":
    unittest.main()

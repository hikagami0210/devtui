import curses
import io
import unittest
from unittest.mock import patch

from devtui.logview import LogView
from devtui.mouse import MouseInput, sgr_scroll


class InputWindow:
    def __init__(self, values):
        self.values = iter(values)
        self.delay = 100

    def timeout(self, delay):
        self.delay = delay

    def get_wch(self):
        try:
            return next(self.values)
        except StopIteration:
            raise curses.error()


class MouseTests(unittest.TestCase):
    def test_sgr_wheel_supports_both_directions_without_button5(self):
        self.assertEqual(sgr_scroll("[<64;20;10M"), (0, -3))
        self.assertEqual(sgr_scroll("[<65;20;10M"), (0, 3))
        self.assertEqual(sgr_scroll("[<66;20;10M"), (-8, 0))
        self.assertEqual(sgr_scroll("[<67;20;10M"), (8, 0))
        self.assertEqual(sgr_scroll("[<68;20;10M"), (-3, 0))

    def test_clicks_and_releases_are_consumed_without_becoming_commands(self):
        self.assertEqual(sgr_scroll("[<0;10;10M"), (0, 0))
        self.assertEqual(sgr_scroll("[<65;10;10m"), (0, 0))
        self.assertIsNone(sgr_scroll("[5~"))

    def test_old_curses_raw_sgr_input_preserves_timeout(self):
        window = InputWindow("[<65;20;10M")
        with patch("curses.unget_wch") as restore:
            self.assertEqual(MouseInput().read(window, "\x1b"), (0, 3))
            restore.assert_not_called()
        self.assertEqual(window.delay, 100)

    def test_escape_followed_by_keyboard_input_is_preserved(self):
        window = InputWindow("q")
        with patch("curses.unget_wch") as restore:
            self.assertIsNone(MouseInput().read(window, "\x1b"))
            restore.assert_called_once_with("q")
        self.assertEqual(window.delay, 100)

    def test_caller_timeout_is_restored_without_getdelay_api(self):
        window = InputWindow("[<64;20;10M")
        window.timeout(55)
        self.assertEqual(MouseInput().read(window, "\x1b", timeout=55), (0, -3))
        self.assertEqual(window.delay, 55)

    def test_partial_mouse_report_restores_all_pending_characters(self):
        window = InputWindow("[<65;")
        with patch("curses.unget_wch") as restore:
            self.assertIsNone(MouseInput().read(window, "\x1b"))
            self.assertEqual([call.args[0] for call in restore.call_args_list], list(reversed("[<65;")))
        self.assertEqual(window.delay, 100)

    def test_native_mouse_events_on_newer_curses(self):
        mouse = MouseInput()
        with patch.object(curses, "BUTTON5_PRESSED", 1 << 40, create=True):
            with patch("curses.getmouse", return_value=(0, 20, 10, 0, curses.BUTTON5_PRESSED)):
                self.assertEqual(mouse.read(InputWindow(""), curses.KEY_MOUSE), (0, 3))
        with patch("curses.getmouse", side_effect=curses.error()):
            self.assertEqual(mouse.read(InputWindow(""), curses.KEY_MOUSE), (0, 0))

    def test_mouse_capture_is_disabled_and_mask_restored(self):
        mouse = MouseInput()
        output = io.StringIO()
        with patch("curses.mousemask", return_value=(1, 123)) as mask, patch("curses.mouseinterval"), patch("sys.stdout", output):
            mouse.enable(True)
            mouse.enable(True)
            mouse.enable(False)
        self.assertEqual(mask.call_count, 2)
        self.assertEqual(mask.call_args.args, (123,))
        self.assertIn("\x1b[?1006h", output.getvalue())
        self.assertTrue(output.getvalue().endswith("\x1b[?1006l"))

    def test_wheel_scroll_freezes_history_and_clamps_position(self):
        view = LogView("Logs", ("server", "api"), ["x" * 200] * 100)
        view.set_viewport(10, 40)
        view.wheel(0, -3)
        self.assertEqual(view.top, 87)
        self.assertFalse(view.follow)
        view.update(["new output"])
        self.assertEqual(len(view.lines), 100)
        view.wheel(0, 999)
        self.assertEqual(view.top, 90)
        view.wheel(0, -999)
        self.assertEqual(view.top, 0)
        view.wheel(8, 0)
        self.assertEqual(view.column, 8)
        view.wheel(-999, 0)
        self.assertEqual(view.column, 0)


if __name__ == "__main__":
    unittest.main()

import curses
from concurrent.futures import Future
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from devtui.config import Config
from devtui.logview import HISTORY_LIMIT, LogView
from devtui.text import slice_cells, text_width
from devtui.tui import UI


class LogViewTests(unittest.TestCase):
    def view(self):
        view = LogView("Logs / api", ("server", "api"), [f"line {i}" for i in range(100)])
        view.set_viewport(10, 40)
        return view

    def test_reading_freezes_output_until_live_tail_is_resumed(self):
        view = self.view()
        self.assertEqual(view.top, 90)
        view.key(curses.KEY_PPAGE)
        self.assertFalse(view.follow)
        self.assertEqual(view.top, 81)
        view.update(["new output"])
        self.assertEqual(view.lines[-1], "line 99")
        self.assertEqual(view.top, 81)
        self.assertEqual(view.key(ord("G")), "refresh")
        view.update(["new output"])
        self.assertEqual(view.lines, ["new output"])
        self.assertEqual(view.top, 0)

    def test_literal_case_insensitive_search_wraps_between_matching_lines(self):
        view = self.view()
        view.lines[10] = "ERROR [db] . failure"
        view.lines[80] = "error [db] . again"
        view.key(ord("/"))
        for char in "error [db] .":
            view.key(ord(char))
        view.key(10)
        self.assertEqual(view.matches, [10, 80])
        self.assertEqual(view.match_index, 0)  # wraps from the tail
        self.assertTrue(view.top <= 10 < view.top + view.height)
        view.key(ord("n"))
        self.assertEqual(view.match_index, 1)
        view.key(ord("N"))
        self.assertEqual(view.match_index, 0)
        view.key(ord("N"))
        self.assertEqual(view.match_index, 1)

    def test_search_reveals_match_beyond_right_edge_and_supports_unicode(self):
        view = LogView("Output", ("job", None), ["a" * 200 + "接続エラー"])
        view.set_viewport(10, 30)
        view.key(ord("/"))
        for char in "接続エラー":
            view.key(-2, char)
        view.key(10)
        self.assertEqual(view.matches, [0])
        self.assertGreater(view.column, 150)
        self.assertIn("接続エラー", slice_cells(view.lines[0], view.column, view.width))

    def test_escape_clears_search_before_closing_view_and_q_can_be_searched(self):
        view = self.view()
        view.key(ord("/"))
        view.key(ord("q"))
        self.assertEqual(view.query, "q")
        self.assertIsNone(view.key(27))
        self.assertFalse(view.editing)
        self.assertEqual(view.query, "")
        self.assertEqual(view.key(27), "close")

    def test_home_end_resize_empty_logs_and_no_matches(self):
        view = self.view()
        view.key(curses.KEY_HOME)
        self.assertEqual(view.top, 0)
        view.set_viewport(200, 40)
        view.key(curses.KEY_NPAGE)
        self.assertEqual(view.top, 0)
        view.key(ord("/"))
        view.key(ord("!"))
        view.key(10)
        self.assertEqual(view.message, "No matches")
        view.key(curses.KEY_END)
        view.update([])
        self.assertEqual(view.top, 0)

    def test_wide_characters_survive_horizontal_clipping(self):
        self.assertEqual(slice_cells("ab日本cd", 2, 4), "日本")
        clipped = slice_cells("ab日本cd", 3, 3)
        self.assertEqual(text_width(clipped), 3)
        self.assertEqual(clipped, " 本")


class FullScreenTests(unittest.TestCase):
    def ui(self):
        ui = UI(Mock(config=Config(Path.cwd() / "mise.toml", (), ("api", "web"))))
        self.addCleanup(ui.pool.shutdown)
        ui.servers = [{"name": "api", "status": "running"}, {"name": "web", "status": "running"}]
        ui.logs = {"api": ["recent"]}
        ui.next_poll = float("inf")
        return ui

    def test_full_screen_loads_older_history_and_does_not_move_server_selection(self):
        ui = self.ui()
        fetched = Future()
        ui.action(ord("f"))
        self.assertFalse(ui.log_view.loaded)
        with patch.object(ui.pool, "submit", return_value=fetched) as submit:
            ui.poll()
            self.assertEqual(submit.call_args.args[-1], HISTORY_LIMIT)
            fetched.set_result({"log": ("api", "\n".join(f"line {i}" for i in range(400))), "log_limit": HISTORY_LIMIT, "errors": []})
            ui.poll()
        self.assertEqual(len(ui.log_view.lines), 400)
        ui.action(ord("k"))
        ui.follow_selected_server()
        self.assertIsNone(ui.log_target)  # paused history stops new log fetches
        self.assertEqual(ui.index[0], 0)
        ui.action(ord("s"))
        ui.backend.daemon_command.assert_not_called()
        ui.action(27)
        self.assertIsNone(ui.log_view)
        self.assertEqual(ui.selected()[0], "api")

    def test_run_output_full_screen_freezes_and_resumes_while_job_runs(self):
        ui = self.ui()
        job = Mock(label="test", status="running")
        job.output.return_value = ["before"]
        ui.jobs.items = [job]
        ui.tab = 3
        ui.action(10)
        self.assertEqual(ui.log_view.lines, ["before"])
        ui.action(ord(" "))
        job.output.return_value = ["before", "after"]
        ui.log_view.update(job.output())
        self.assertEqual(ui.log_view.lines, ["before"])
        ui.action(ord("G"))
        ui.log_view.update(job.output())
        self.assertEqual(ui.log_view.lines[-1], "after")
        ui.action(ord("q"))
        self.assertIsNone(ui.log_view)


if __name__ == "__main__":
    unittest.main()

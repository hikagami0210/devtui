import contextlib
import io
import json
from pathlib import Path
from string import Formatter
import tempfile
import unittest
from unittest.mock import Mock, patch

from devtui.__main__ import main
from devtui.config import Config, Repo, Script, UserError, load
from devtui.i18n import JAPANESE, status_text, translate
from devtui.logview import LogView
from devtui.text import cell_width
from devtui.tui import UI


class Screen:
    """A cell grid that rejects writes outside the terminal."""
    def __init__(self, width, height):
        self.width, self.height = width, height
        self.erase()

    def erase(self):
        self.grid = [[" "] * self.width for _ in range(self.height)]

    def getmaxyx(self):
        return self.height, self.width

    def addstr(self, y, x, text, style=0):
        assert 0 <= y < self.height and 0 <= x < self.width, (y, x)
        for char in text:
            size = cell_width(char)
            if not size:
                continue
            assert x + size <= self.width, (y, x, text)
            self.grid[y][x] = char
            if size == 2:
                self.grid[y][x + 1] = ""
            x += size

    def refresh(self):
        pass

    def text(self):
        return "\n".join("".join(row) for row in self.grid)


class LanguageTests(unittest.TestCase):
    def ui(self, language="ja"):
        config = Config(Path("/workspace/devtui/mise.toml"), (), ("api",), language=language)
        ui = UI(Mock(config=config))
        self.addCleanup(ui.pool.shutdown)
        ui.servers = [{"name": "api", "status": "running", "port": 5432, "branch": "機能/ログ"}]
        ui.tasks = [{"name": "test", "description": "Original task description", "interactive": True}]
        ui.scripts = [Script(Repo("api", config.root), "start", "node server.js", "npm")]
        job = Mock(label="Mise tasks:test", status="exit 0")
        job.done.is_set.return_value = True
        job.output.return_value = ["Original task output {id}"]
        ui.jobs.items = [job]
        ui.logs = {"api": ["Original log {name}"]}
        return ui

    def test_catalog_has_matching_format_fields(self):
        def fields(template):
            return {name for _, name, _, _ in Formatter().parse(template) if name}
        for english, japanese in JAPANESE.items():
            with self.subTest(template=english):
                self.assertEqual(fields(english), fields(japanese))
                values = dict.fromkeys(fields(english), "値")
                self.assertEqual(translate("en", english, **values), english.format(**values))
                self.assertEqual(translate("ja", english, **values), japanese.format(**values))
        self.assertEqual(status_text("ja", "exit 17"), "終了 17")
        self.assertEqual(status_text("ja", "future {status}"), "future {status}")

    def test_config_language_and_cli_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mise.toml"
            path.write_text('[_.devtui]\nlanguage="ja"\n')
            self.assertEqual(load(path).language, "ja")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main([directory, "--lang", "en", "--inspect"]), 0)
            self.assertEqual(json.loads(output.getvalue())["language"], "en")
            path.write_text('[_.devtui]\n')
            self.assertEqual(load(path).language, "en")
            for value in ('"invalid"', 'false', '[]'):
                path.write_text('[_.devtui]\nlanguage=' + value)
                with self.assertRaises(UserError):
                    load(path)
        with patch("devtui.__main__.os.execv") as execute:
            main(["--demo", "--lang", "ja"])
            self.assertEqual(execute.call_args.args[1][-2:], ["--lang", "ja"])

    def test_language_toggle_preserves_view_filter_and_frozen_log(self):
        ui = self.ui("en")
        ui.query = "api"
        ui.action(ord("L"))
        self.assertEqual(ui.language, "ja")
        self.assertEqual(ui.query, "api")
        ui.action(ord("f"))
        view = ui.log_view
        view.loaded = True
        view.follow = False
        view.query = "Original"
        view.find_matches()
        view.jump()
        before = (view.top, view.column, view.query, list(view.lines), list(view.matches))
        ui.action(ord("L"))
        self.assertEqual((ui.language, view.language), ("en", "en"))
        self.assertIs(ui.log_view, view)
        self.assertEqual((view.top, view.column, view.query, view.lines, view.matches), before)
        self.assertFalse(view.follow)
        ui.backend.daemon_command.assert_not_called()
        ui.backend.clear_logs.assert_not_called()

    def test_L_is_text_while_filtering_or_searching(self):
        ui = self.ui()
        ui.action(ord("/"))
        ui.action(ord("L"), "L")
        self.assertEqual((ui.language, ui.query), ("ja", "L"))
        ui.action(27)
        ui.action(ord("f"))
        ui.log_view.loaded = True
        ui.action(ord("/"))
        ui.action(ord("L"), "L")
        self.assertEqual((ui.language, ui.log_view.query), ("ja", "L"))

    def test_japanese_copy_and_search_feedback(self):
        ui = self.ui()
        ui.action(ord("f"))
        ui.log_view.loaded = True
        with patch("devtui.tui.copy_text") as copy:
            ui.action(ord("C"))
            copy.assert_called_once_with("Original log {name}\n")
        self.assertEqual(ui.log_view.message, "1行をコピーしました（読み込み済み全体）")
        ui.action(ord("/"))
        ui.action(ord("!"))
        ui.action(10)
        self.assertEqual(ui.log_view.message, "一致する行がありません")

    def test_ui_override_has_priority_over_config(self):
        ui = self.ui()
        english = UI(ui.backend, language="en")
        self.addCleanup(english.pool.shutdown)
        self.assertEqual(english.message, "Loading...")
        self.assertEqual(ui.message, "読み込み中…")

    def test_both_languages_fit_four_views_and_fullscreen_at_supported_sizes(self):
        for language in ("ja", "en"):
            ui = self.ui(language)
            for width, height in ((68, 16), (80, 24), (110, 30), (128, 34), (180, 48)):
                for tab in range(4):
                    with self.subTest(language=language, width=width, tab=tab):
                        ui.tab = tab
                        screen = Screen(width, height)
                        ui.draw(screen)
                        for y in range(7, height - 2):
                            self.assertIn(screen.grid[y][0], "┌└│")
                            self.assertIn(screen.grid[y][-1], "┐┘│")
                        self.assertIn(translate(language, ui.tabs[tab]), "".join(screen.grid[7]))
                        self.assertIn("<L>", "".join(screen.grid[-1]))
                        self.assertIn("<q>", "".join(screen.grid[-1]))
                ui.help = True
                ui.draw(Screen(width, height))
                ui.help = False
            ui.tab = 0
            ui.action(ord("f"))
            ui.log_view.loaded = True
            ui.log_view.query = "Original"
            ui.log_view.find_matches()
            for width, height in ((40, 10), (68, 16), (80, 24), (110, 30), (128, 34), (180, 48)):
                with self.subTest(language=language, width=width, fullscreen=True):
                    screen = Screen(width, height)
                    ui.draw(screen)
                    for y in range(height - 2):
                        self.assertIn(screen.grid[y][0], "┌└│")
                        self.assertIn(screen.grid[y][-1], "┐┘│")
                    self.assertIn("Original log {name}", screen.text())
                    self.assertIn(translate(language, "FOLLOW"), "".join(screen.grid[0]))


if __name__ == "__main__":
    unittest.main()

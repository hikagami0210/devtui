import json
import os
import shutil
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from concurrent.futures import Future
from unittest.mock import Mock, patch

from devtui.backend import Mise
from devtui.config import Config, Repo, Script, UserError, load, scripts
from devtui.process import Jobs, capture
from devtui.git import current_branch
from devtui.__main__ import config_path, main


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="devtui command ")
        self.addCleanup(self.temporary.cleanup)
        self.workspace = Path(self.temporary.name)
        self.project = Path(__file__).resolve().parents[1]
        (self.workspace / "mise.toml").write_text('[_.devtui]\nexit_policy="keep"\n')
        self.env = dict(os.environ, DEVTUI_PYTHON=sys.executable)

    def test_launcher_preserves_current_directory_even_through_symlink(self):
        link = self.workspace / "devtui"
        link.symlink_to(self.project / "run")
        (self.workspace / "devtui.py").write_text('raise RuntimeError("wrong import")')
        output = capture([str(link), "--inspect"], self.workspace, env=self.env)
        self.assertEqual(json.loads(output)["root"], str(self.workspace.resolve()))

    def test_directory_and_config_file_arguments_from_another_directory(self):
        for argument in (self.workspace, self.workspace / "mise.toml"):
            output = capture([str(self.project / "run"), str(argument), "--inspect"], self.project, env=self.env)
            self.assertEqual(json.loads(output)["root"], str(self.workspace.resolve()))

    def test_missing_config_has_actionable_error(self):
        empty = self.workspace / "empty"
        empty.mkdir()
        with self.assertRaises(UserError) as raised:
            capture([str(self.project / "run"), str(empty), "--inspect"], self.workspace, env=self.env)
        self.assertIn("mise.toml がありません", str(raised.exception))
        self.assertIn("devtui --demo", str(raised.exception))

    def test_home_directory_argument_expands(self):
        self.assertEqual(config_path(Path("~")), Path.home().resolve() / "mise.toml")

    def test_demo_delegates_to_isolated_launcher(self):
        with patch("devtui.__main__.os.execv") as execute:
            self.assertEqual(main(["--demo", "--inspect", "--exit-policy", "stop"]), 0)
        execute.assert_called_once_with(str(self.project / "try-demo"),
            [str(self.project / "try-demo"), "--inspect", "--exit-policy", "stop"])

    def test_install_is_idempotent_and_preserves_existing_commands(self):
        directory = self.workspace / "bin space"
        install = self.project / "install"
        for _ in range(2):
            capture([str(install), str(directory)], self.workspace)
        link = directory / "devtui"
        self.assertEqual(link.resolve(), (self.project / "run").resolve())
        link.unlink()
        link.write_text("existing command")
        with self.assertRaises(UserError):
            capture([str(install), str(directory)], self.workspace)
        self.assertEqual(link.read_text(), "existing command")

    def test_demo_runs_from_relocated_source_without_machine_local_files(self):
        relocated = self.workspace / "relocated app"
        relocated.mkdir()
        for name in ("run", "launch.py", "try-demo", "mise.toml"):
            shutil.copy2(self.project / name, relocated / name)
        for name in ("devtui", "examples"):
            shutil.copytree(self.project / name, relocated / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        self.assertFalse((relocated / ".devtui-demo.local.sh").exists())
        # --inspect never executes mise; use an existing binary to avoid relying
        # on any installed mise or the original checkout's private tool setup.
        env = dict(self.env, DEVTUI_MISE=sys.executable)
        output = capture([str(relocated / "run"), "--demo", "--inspect"], self.workspace, env=env)
        result = json.loads(output)
        self.assertEqual(result["root"], str(relocated.resolve()))
        self.assertEqual(len(result["daemons"]), 5)
        self.assertEqual(len(result["scripts"]), 12)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_dynamic_discovery_and_missing_repo(self):
        repo = self.root / "repo"
        repo.mkdir()
        package = repo / "package.json"
        package.write_text(json.dumps({"packageManager": "yarn@4.0.0", "scripts": {"test": "echo test"}}))
        path = self.root / "mise.toml"
        path.write_text('[_.devtui.repos.app]\npath="repo"\n[_.devtui.repos.missing]\npath="missing"\n')
        config = load(path)
        values, errors = scripts(config)
        self.assertEqual([(s.name, s.manager) for s in values], [("test", "yarn")])
        self.assertIn("missing", errors[0])
        package.write_text('{"scripts":{"build":"echo build"}}')
        self.assertEqual(scripts(config)[0][0].name, "build")

    def test_ambiguous_manager_and_override(self):
        (self.root / "package.json").write_text('{"scripts":{"test":"echo yes"}}')
        (self.root / "yarn.lock").touch()
        (self.root / "pnpm-lock.yaml").touch()
        config = Config(self.root / "mise.toml", (Repo("a", self.root),), ())
        self.assertTrue(scripts(config)[1])
        config = Config(config.path, (Repo("a", self.root, "pnpm"),), ())
        self.assertEqual(scripts(config)[0][0].manager, "pnpm")

    def test_bad_config_is_actionable(self):
        path = self.root / "mise.toml"
        for text in ('[_.devtui]\nexit_policy="bad"', '[_.devtui.repos.a]\npath=1', '[daemons."--all"]\nrun="bad"'):
            path.write_text(text)
            with self.assertRaises(UserError):
                load(path)

    def test_explicit_daemon_mapping_is_validated(self):
        path = self.root / "mise.toml"
        path.write_text('[_.devtui.repos.app]\npath="repo"\ndaemons=["api","worker"]\n[daemons.api]\nrun="true"\n[daemons.worker]\nrun="true"\n')
        config = load(path)
        self.assertEqual(config.repos[0].daemons, ("api", "worker"))
        path.write_text(path.read_text().replace('"worker"]', '"missing"]'))
        with self.assertRaises(UserError):
            load(path)


class GitTests(unittest.TestCase):
    def test_unborn_branch_and_external_switch_are_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture(["git", "init", "-b", "main"], root)
            self.assertEqual(current_branch(root), "main")
            capture(["git", "checkout", "-b", "feature/branch-column"], root)
            self.assertEqual(current_branch(root), "feature/branch-column")
            with patch.dict(os.environ, {"GIT_DIR": "/nonexistent", "GIT_WORK_TREE": "/nonexistent"}):
                self.assertEqual(current_branch(root), "feature/branch-column")

    def test_detached_checkout_and_non_git_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(current_branch(root), "-")
            capture(["git", "init", "-b", "main"], root)
            capture(["git", "-c", "user.name=DevTUI Test", "-c", "user.email=test@example.invalid",
                     "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
                     "commit", "--allow-empty", "-m", "test"], root)
            commit = capture(["git", "rev-parse", "--short", "HEAD"], root).strip()
            capture(["git", "checkout", "--detach", "HEAD"], root)
            self.assertEqual(current_branch(root), f"detached@{commit}")


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.jobs = Jobs()
        self.addCleanup(self.jobs.close)

    def start(self, code, **kwargs):
        return self.jobs.start("test", [sys.executable, "-u", "-c", code], Path.cwd(), dict(os.environ), **kwargs)

    def test_exit_codes_and_partial_line(self):
        job = self.start("import sys; print('failure',end=''); sys.exit(7)")
        self.assertTrue(job.done.wait(5))
        self.assertEqual(job.code, 7)
        self.assertIn("failure", job.output())

    def test_log_memory_is_bounded(self):
        job = self.start("print('x'*200000); print('line\\n'*4000)")
        self.assertTrue(job.done.wait(5))
        self.assertLessEqual(len(job.output()), 1500)
        self.assertLessEqual(max(map(len, job.output())), 12288)

    def test_cancel_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / "pid"
            code = f"import subprocess,time; p=subprocess.Popen([{sys.executable!r},'-c','import time; time.sleep(60)']); open({str(pidfile)!r},'w').write(str(p.pid)); time.sleep(60)"
            job = self.start(code)
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(pidfile.exists())
            child = int(pidfile.read_text())
            job.cancel()
            self.assertTrue(job.done.wait(5))
            self.assertEqual(job.status, "cancelled")
            # A zombie is no longer executing; ps distinguishes it from a live child.
            import subprocess
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
            self.assertTrue(not state or state.startswith("Z"), state)

    def test_timeout_and_duplicate_run(self):
        job = self.start("import time; time.sleep(60)", timeout=0.2)
        with self.assertRaises(UserError):
            self.start("pass")
        self.assertTrue(job.done.wait(5))
        self.assertNotEqual(job.code, 0)

    def test_capture_timeout(self):
        with self.assertRaises(UserError):
            capture([sys.executable, "-c", "import time; time.sleep(60)"], Path.cwd(), timeout=0.1)


class BackendTests(unittest.TestCase):
    def backend(self, path=None):
        config = Config(path or Path.cwd() / "mise.toml", (), ("api",))
        with patch.dict(os.environ, {}, clear=True):
            return Mise(config, sys.executable)

    def test_server_filter_excludes_foreign_and_removed(self):
        backend = self.backend()
        data = [{"name": "api", "root": str(backend.config.root), "status": "running"},
                {"name": "api", "root": "/foreign", "status": "running"},
                {"name": "removed", "root": str(backend.config.root)}]
        with patch.object(backend, "read", return_value=json.dumps(data)):
            self.assertEqual(backend.servers(), [dict(data[0], branch="-")])

    def test_branch_mapping_supports_multiple_daemons_per_repo(self):
        backend = self.backend()
        backend.config = Config(backend.config.path, (Repo("app", Path.cwd(), daemons=("api", "worker")),), ("api", "worker"))
        data = [{"name": name, "root": str(backend.config.root)} for name in ("api", "worker")]
        with patch.object(backend, "read", return_value=json.dumps(data)), patch("devtui.backend.current_branch", return_value="feature/shared") as branch:
            self.assertEqual([s["branch"] for s in backend.servers()], ["feature/shared"] * 2)
            branch.assert_called_once_with(Path.cwd())

    def test_package_argv_does_not_interpolate_shell_input(self):
        backend = self.backend()
        repo = Repo("odd", Path("/tmp/spaces ' $(touch bad)"))
        script = Script(repo, "test; echo bad", "ignored", "npm")
        args = backend.script_command(script)
        self.assertEqual(args[-4:], [str(repo.path), "npm", "run", script.name])
        self.assertNotIn(str(repo.path), args[args.index("-c") + 1])

    def test_empty_stop_does_not_target_global_daemons(self):
        backend = self.backend()
        backend.config = Config(backend.config.path, (), ())
        with patch.object(backend, "read") as read:
            backend.stop_all()
            read.assert_not_called()

    def test_old_mise_rejected(self):
        backend = self.backend()
        with patch.object(backend, "read", return_value="2025.12.12 macos-arm64"):
            with self.assertRaises(UserError):
                backend.doctor()

    def test_server_logs_distinguish_repeated_messages_by_timestamp(self):
        backend = self.backend()
        entries = [
            {"timestamp": "2026-11-16 13:26:35", "message": "api ready"},
            {"timestamp": "2026-11-16 14:14:28", "message": "api ready"},
            {"timestamp": "2026-11-16 14:14:29", "message": "first\nsecond"},
        ]
        with patch.object(backend, "read", return_value=json.dumps(entries)) as read:
            first = backend.logs("api")
            self.assertEqual(first, backend.logs("api"))
            self.assertIn("--json", read.call_args.args)
        self.assertEqual(first.splitlines(), [
            "[2026-11-16 13:26:35] api ready",
            "[2026-11-16 14:14:28] api ready",
            "[2026-11-16 14:14:29] first",
            "[2026-11-16 14:14:29] second",
        ])

    def test_clear_logs_only_accepts_a_declared_server(self):
        backend = self.backend()
        with patch.object(backend, "read", return_value="") as read:
            for name in ("", "--all", "foreign"):
                with self.assertRaises(UserError):
                    backend.clear_logs(name)
            read.assert_not_called()
            backend.clear_logs("api")
            read.assert_called_once_with("daemons", "logs", "api", "--clear")


class LogClearTests(unittest.TestCase):
    def ui(self):
        from devtui.tui import UI
        backend = Mock(config=Config(Path.cwd() / "mise.toml", (), ("api", "web")))
        ui = UI(backend)
        self.addCleanup(ui.pool.shutdown)
        ui.servers = [{"name": "api", "status": "running"}]
        ui.logs = {"api": ["old api log"], "web": ["keep web log"]}
        ui.next_poll = float("inf")
        return ui

    def test_clear_discards_inflight_snapshot_and_preserves_other_server(self):
        ui = self.ui()
        snapshot, cleared, refreshed = Future(), Future(), Future()
        snapshot.set_result({"log": ("api", "old fetched log"), "errors": []})
        ui.future = snapshot
        ui.offset = 8
        with patch.object(ui.pool, "submit", side_effect=[cleared, refreshed]) as submit:
            ui.action(ord("C"))
            submit.assert_called_once_with(ui.backend.clear_logs, "api")
            cleared.set_result("")
            ui.poll()
        self.assertEqual(ui.logs, {"api": [], "web": ["keep web log"]})
        self.assertEqual(ui.offset, 0)
        self.assertIn("Cleared", ui.message)
        self.assertEqual(ui.log_target, "api")

    def test_failed_clear_keeps_cached_logs_and_reports_error(self):
        ui = self.ui()
        cleared, refreshed = Future(), Future()
        with patch.object(ui.pool, "submit", side_effect=[cleared, refreshed]):
            ui.action(ord("C"))
            cleared.set_exception(UserError("unavailable"))
            ui.poll()
        self.assertEqual(ui.logs["api"], ["old api log"])
        self.assertIn("Log clear failed", ui.message)

    def test_escape_cancels_filter_and_returns_to_server_shortcuts(self):
        ui = self.ui()
        ui.action(ord("/"))
        for char in "no-matching-server":
            ui.action(ord(char))
        self.assertTrue(ui.editing)
        self.assertEqual(ui.rows(), [])
        ui.action(27)
        self.assertFalse(ui.editing)
        self.assertEqual(ui.query, "")
        self.assertEqual(len(ui.rows()), 1)
        with patch.object(ui, "start") as start:
            ui.action(ord("s"))
            self.assertEqual(start.call_args.args[0], "server:start:api")

    def test_enter_applies_branch_filter_without_executing_a_server(self):
        ui = self.ui()
        ui.servers[0]["branch"] = "feature/search"
        ui.action(ord("/"))
        for char in "feature/search":
            ui.action(ord(char))
        with patch.object(ui, "start") as start:
            ui.action(10)
            start.assert_not_called()
        self.assertFalse(ui.editing)
        self.assertEqual(ui.query, "feature/search")
        self.assertEqual(ui.selected()[0], "api")

    def test_first_loaded_server_is_followed_without_log_key(self):
        ui = self.ui()
        ui.servers = []
        first, second = Future(), Future()
        ui.next_poll = 0
        with patch.object(ui.pool, "submit", side_effect=[first, second]) as submit:
            ui.poll()
            self.assertIsNone(submit.call_args.args[2])
            first.set_result({"servers": [{"name": "api", "status": "running"}], "errors": []})
            ui.poll()
            self.assertEqual(ui.log_target, "api")
            self.assertEqual(submit.call_args.args[2], "api")

    def test_selection_switches_follow_target_even_during_a_log_read(self):
        ui = self.ui()
        ui.servers.append({"name": "web", "status": "running"})
        ui.logs = {}
        first, second, third = Future(), Future(), Future()
        with patch.object(ui.pool, "submit", side_effect=[first, second, third]) as submit:
            ui.poll()
            self.assertEqual(submit.call_args.args[2], "api")
            ui.action(ord("j"))
            ui.poll()
            self.assertEqual(ui.log_target, "web")
            first.set_result({"log": ("api", "api output"), "errors": []})
            ui.poll()
            self.assertEqual(submit.call_args.args[2], "web")
            self.assertNotIn("web", ui.logs)
            ui.action(ord("2"))
            second.set_result({"log": ("web", "web output"), "errors": []})
            ui.next_poll = 0
            ui.poll()
            self.assertIsNone(submit.call_args.args[2])
            self.assertIsNone(ui.log_target)


if __name__ == "__main__":
    unittest.main()

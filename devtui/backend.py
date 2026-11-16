import json
import os
import re
import shutil

from .config import UserError
from .process import capture
from .git import current_branch


class Mise:
    def __init__(self, config, binary="mise"):
        self.config = config
        self.binary = shutil.which(binary)
        if not self.binary:
            raise UserError(f"miseが見つかりません: {binary}")
        self.env = dict(os.environ, MISE_EXPERIMENTAL="1", MISE_COLOR="0",
                        NO_COLOR="1", TERM="dumb", PAGER="cat", GIT_PAGER="cat")
        self.env["PATH"] = os.path.dirname(self.binary) + os.pathsep + self.env.get("PATH", "")
        # Keep TUI inspection and execution in the same ordinary root configuration.
        for key in ("MISE_ENV", "MISE_DEFAULT_CONFIG_FILENAME", "MISE_OVERRIDE_CONFIG_FILENAMES"):
            if self.env.get(key):
                raise UserError(f"MVPでは {key} を解除して単一mise.tomlを使用してください")

    def command(self, *args):
        return [self.binary, "-C", str(self.config.root), *args]

    def read(self, *args, timeout=12):
        return capture(self.command(*args), self.config.root, timeout, self.env)

    def doctor(self):
        version = self.read("version")
        match = re.search(r"(\d{4})\.(\d+)\.(\d+)", version)
        if not match or tuple(map(int, match.groups())) < (2026, 11, 8):
            raise UserError(f"mise >= 2026.11.8 が必要です。現在: {version.strip()}")
        return version.strip()

    def servers(self):
        data = json.loads(self.read("daemons", "ls", "--json"))
        if not isinstance(data, list) or any(not isinstance(x, dict) or "name" not in x for x in data):
            raise UserError("mise daemons ls のJSON形式に対応していません")
        # Only operate on declarations in this file, never foreign project dependencies.
        servers = [dict(x) for x in data if x["name"] in self.config.daemons
                   and x.get("root") == str(self.config.root)]
        repo_by_daemon = {name: repo for repo in self.config.repos
                          for name in (repo.daemons or (repo.name,))}
        branches = {}
        for server in servers:
            repo = repo_by_daemon.get(server["name"])
            if repo and repo.path not in branches:
                branches[repo.path] = current_branch(repo.path)
            server["branch"] = branches[repo.path] if repo else "-"
        return servers

    def tasks(self):
        data = json.loads(self.read("tasks", "ls", "--local", "--json"))
        if not isinstance(data, list) or any(not isinstance(x, dict) or "name" not in x for x in data):
            raise UserError("mise tasks ls のJSON形式に対応していません")
        return [x for x in data if not x.get("hide") and not x["name"].startswith("-")]

    def logs(self, name):
        entries = json.loads(self.read("daemons", "logs", name, "-n", "150", "--json"))
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("timestamp"), str)
            or not isinstance(entry.get("message"), str) for entry in entries
        ):
            raise UserError("mise daemons logs のJSON形式に対応していません")
        # pitchfork retains previous starts. Preserve their timestamps rather than
        # making independent occurrences of the same message look like duplicates.
        return "\n".join(
            f'[{entry["timestamp"]}] {line}'
            for entry in entries
            for line in (entry["message"].splitlines() or [""])
        )

    def daemon_command(self, action, name):
        if action not in ("start", "stop", "restart") or name not in self.config.daemons:
            raise UserError("不明なサーバー操作です")
        return self.command("daemons", action, name)

    def clear_logs(self, name):
        if name not in self.config.daemons:
            raise UserError("ログ削除対象のサーバーが不明です")
        return self.read("daemons", "logs", name, "--clear")

    def script_command(self, script):
        # Root mise supplies the environment; argv supplies repo/name without shell interpolation.
        return self.command("exec", "--", "sh", "-c", 'cd "$1" && shift && exec "$@"',
                            "devtui", str(script.repo.path), script.manager, "run", script.name)

    def stop_all(self):
        # An empty selector would mean every daemon, so never issue it.
        if self.config.daemons:
            return self.read("daemons", "stop", *self.config.daemons, timeout=90)
        return ""

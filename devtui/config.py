from dataclasses import dataclass
import json
from pathlib import Path
import re
import tomllib

HISTORY_LIMIT = 5000


class UserError(Exception):
    pass


@dataclass(frozen=True)
class Repo:
    name: str
    path: Path
    manager: str | None = None
    daemons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Script:
    repo: Repo
    name: str
    command: str
    manager: str


@dataclass(frozen=True)
class Config:
    path: Path
    repos: tuple[Repo, ...]
    daemons: tuple[str, ...]
    exit_policy: str = "keep"
    language: str = "en"

    @property
    def root(self):
        return self.path.parent


def load(path: Path) -> Config:
    path = path.expanduser().resolve()
    if path.name != "mise.toml":
        raise UserError("MVPでは mise.toml を指定してください（overlay/includeは未対応）。")
    try:
        data = tomllib.loads(path.read_text())
        options = data.get("_", {}).get("devtui", {})
        if not isinstance(options, dict):
            raise ValueError("_.devtui must be a table")
        policy = options.get("exit_policy", "keep")
        if policy not in ("keep", "stop"):
            raise ValueError("exit_policy must be keep or stop")
        language = options.get("language", "en")
        if language not in ("ja", "en"):
            raise ValueError("language must be ja or en")
        repos = []
        for name, entry in options.get("repos", {}).items():
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise ValueError(f"repo {name}: path is required")
            manager = entry.get("package_manager")
            if manager is not None and manager not in ("npm", "yarn", "pnpm", "bun"):
                raise ValueError(f"repo {name}: unsupported package_manager")
            names = entry.get("daemons", [])
            if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
                raise ValueError(f"repo {name}: daemons must be an array of names")
            repos.append(Repo(name, (path.parent / Path(entry["path"]).expanduser()).resolve(), manager, tuple(names)))
        daemons = data.get("daemons", {})
        if not isinstance(daemons, dict):
            raise ValueError("daemons must be a table")
        for name in daemons:
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", name) or ".." in name or "--" in name or name.endswith("-"):
                raise ValueError(f"invalid daemon name: {name}")
        assigned = set()
        for repo in repos:
            for name in repo.daemons or ((repo.name,) if repo.name in daemons else ()):
                if name not in daemons or name in assigned:
                    raise ValueError(f"repo {repo.name}: unknown or duplicate daemon mapping: {name}")
                assigned.add(name)
        return Config(path, tuple(repos), tuple(daemons), policy, language)
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise UserError(f"設定を読めません: {path}: {exc}") from exc


def scripts(config: Config):
    found, errors = [], []
    locks = {"package-lock.json": "npm", "npm-shrinkwrap.json": "npm",
             "yarn.lock": "yarn", "pnpm-lock.yaml": "pnpm", "bun.lock": "bun", "bun.lockb": "bun"}
    for repo in config.repos:
        try:
            package = json.loads((repo.path / "package.json").read_text())
            values = package.get("scripts", {})
            if not isinstance(values, dict) or any(not isinstance(v, str) for v in values.values()):
                raise ValueError("scripts must map names to strings")
            declared = package.get("packageManager", "")
            if not isinstance(declared, str):
                raise ValueError("packageManager must be a string")
            manager = repo.manager or declared.split("@")[0]
            if not manager:
                candidates = {value for key, value in locks.items() if (repo.path / key).exists()}
                if len(candidates) > 1:
                    raise ValueError("lockfileが複数種類あります。package_managerを指定してください")
                manager = next(iter(candidates), "npm")
            if manager not in ("npm", "yarn", "pnpm", "bun"):
                raise ValueError(f"unsupported packageManager: {manager}")
            for name, command in sorted(values.items()):
                if not name or name.startswith("-"):
                    raise ValueError(f"invalid script name: {name!r}")
                found.append(Script(repo, name, command, manager))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            errors.append(f"{repo.name}: {exc}")
    return found, errors

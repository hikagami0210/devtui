import argparse
import json
import os
from pathlib import Path
import sys

from .backend import Mise
from .config import UserError, load, scripts


def config_path(workspace=None, explicit=None):
    if explicit is not None:
        return explicit.expanduser().resolve()
    location = (workspace if workspace is not None else Path.cwd()).expanduser().resolve()
    return location if location.name == "mise.toml" and not location.is_dir() else location / "mise.toml"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="devtui", description="Development environment TUI backed by mise + pitchfork")
    parser.add_argument("workspace", nargs="?", type=Path, help="workspace directory (default: current directory), or mise.toml")
    parser.add_argument("--config", type=Path, help="explicit mise.toml path")
    parser.add_argument("--demo", action="store_true", help="open this checkout's isolated five-server demo")
    parser.add_argument("--mise", default="mise", help="mise executable name or path")
    parser.add_argument("--exit-policy", choices=("keep", "stop"))
    parser.add_argument("--lang", choices=("ja", "en"), help="initial UI language (overrides mise.toml)")
    parser.add_argument("--inspect", action="store_true", help="read local config/package.json as JSON; no commands executed")
    parser.add_argument("--doctor", action="store_true", help="check mise and read daemon/task lists")
    args = parser.parse_args(argv)
    if args.workspace is not None and args.config is not None:
        parser.error("ディレクトリと --config はどちらか一方を指定してください")
    if args.demo:
        if args.workspace is not None or args.config is not None or args.mise != "mise":
            parser.error("--demo はディレクトリ・--config・--mise と併用できません")
        demo = Path(__file__).resolve().parent.parent / "try-demo"
        forwarded = [str(demo)]
        if args.inspect:
            forwarded.append("--inspect")
        if args.doctor:
            forwarded.append("--doctor")
        if args.exit_policy:
            forwarded.extend(("--exit-policy", args.exit_policy))
        if args.lang:
            forwarded.extend(("--lang", args.lang))
        try:
            os.execv(str(demo), forwarded)
        except OSError as exc:
            print(f"devtui: デモを起動できません: {exc}", file=sys.stderr)
            return 1
        return 0
    try:
        path = config_path(args.workspace, args.config)
        if not path.is_file():
            raise UserError(f"mise.toml がありません: {path}\n"
                            "devtui <設定のあるディレクトリ> を指定してください。デモは devtui --demo で起動できます。")
        config = load(path)
        if args.inspect:
            found, errors = scripts(config)
            print(json.dumps({"root": str(config.root), "exit_policy": args.exit_policy or config.exit_policy,
                "language": args.lang or config.language,
                "daemons": config.daemons,
                "scripts": [{"repo": s.repo.name, "name": s.name, "manager": s.manager, "command": s.command} for s in found],
                "errors": errors}, ensure_ascii=False, indent=2))
            return int(bool(errors))
        backend = Mise(config, args.mise)
        version = backend.doctor()
        if args.doctor:
            print(json.dumps({"mise": version, "servers": backend.servers(), "tasks": backend.tasks()}, indent=2))
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise UserError("TUIは端末から起動してください。確認のみは --inspect / --doctor を使えます。")
        from .tui import UI
        UI(backend, args.exit_policy, language=args.lang).run()
        return 0
    except (UserError, ValueError) as exc:
        print(f"devtui: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

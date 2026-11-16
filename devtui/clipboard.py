"""Copy text through the local macOS clipboard, independent of the terminal."""
import os
import subprocess
import sys

from .config import UserError


def copy_text(text):
    if sys.platform != "darwin":
        raise UserError("クリップボードコピーは現在macOSのみ対応しています")
    environment = dict(os.environ, LC_CTYPE="UTF-8")
    environment.pop("LC_ALL", None)
    try:
        result = subprocess.run(["/usr/bin/pbcopy"], input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=2, env=environment)
    except subprocess.TimeoutExpired as exc:
        raise UserError("クリップボードへのコピーがタイムアウトしました") from exc
    except OSError as exc:
        raise UserError("クリップボードを利用できません") from exc
    if result.returncode:
        raise UserError(f"クリップボードへのコピーに失敗しました (exit {result.returncode})")

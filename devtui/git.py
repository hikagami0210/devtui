import os
from pathlib import Path

from .config import UserError
from .process import capture


def current_branch(path: Path) -> str:
    """Read the checkout on disk, including unborn branches and detached HEADs."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    try:
        return capture(["git", "symbolic-ref", "--quiet", "--short", "HEAD"], path,
                       timeout=2, env=env).strip() or "-"
    except UserError:
        try:
            commit = capture(["git", "rev-parse", "--short", "HEAD"], path,
                             timeout=2, env=env).strip()
            return f"detached@{commit}" if commit else "-"
        except UserError:
            return "-"

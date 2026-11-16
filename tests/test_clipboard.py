import subprocess
import unittest
from unittest.mock import Mock, patch

from devtui.clipboard import copy_text
from devtui.config import UserError


class ClipboardTests(unittest.TestCase):
    def test_unicode_is_sent_to_pbcopy_over_stdin_without_a_shell(self):
        with patch("devtui.clipboard.sys.platform", "darwin"), \
                patch.dict("os.environ", {"LC_ALL": "C"}), \
                patch("devtui.clipboard.subprocess.run", return_value=Mock(returncode=0)) as run:
            copy_text("日本語ログ\n$(literal)\n")
        self.assertEqual(run.call_args.args, (["/usr/bin/pbcopy"],))
        self.assertEqual(run.call_args.kwargs["input"], "日本語ログ\n$(literal)\n".encode())
        self.assertEqual(run.call_args.kwargs["env"]["LC_CTYPE"], "UTF-8")
        self.assertNotIn("LC_ALL", run.call_args.kwargs["env"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_failures_are_reported_without_claiming_success(self):
        with patch("devtui.clipboard.sys.platform", "darwin"):
            for failure in (OSError(), subprocess.TimeoutExpired("pbcopy", 2)):
                with self.subTest(failure=failure), \
                        patch("devtui.clipboard.subprocess.run", side_effect=failure):
                    with self.assertRaises(UserError):
                        copy_text("log")
            with patch("devtui.clipboard.subprocess.run", return_value=Mock(returncode=1)):
                with self.assertRaisesRegex(UserError, "exit 1"):
                    copy_text("log")

    def test_unsupported_platform_does_not_run_commands(self):
        with patch("devtui.clipboard.sys.platform", "linux"), \
                patch("devtui.clipboard.subprocess.run") as run:
            with self.assertRaisesRegex(UserError, "macOS"):
                copy_text("log")
            run.assert_not_called()

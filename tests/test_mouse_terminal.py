"""Exercise real curses with a modern SGR terminfo entry, including old macOS."""
import curses
import fcntl
import json
import os
from pathlib import Path
import pty
import select
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest


def mouse_probe():
    """Test subprocess entry point; uses production input and log rendering."""
    from devtui.logview import LogView
    from devtui.mouse import MouseInput
    events = []

    def loop(win):
        view = LogView("Regression test", ("test", None), [f"Log {i}" for i in range(600)])
        view.follow, view.top = False, 280
        win.timeout(100)
        mouse = MouseInput()
        try:
            mouse.enable(True)
            while True:
                view.draw(win, lambda _: 0)
                try:
                    char, event = mouse.get_input(win)
                except curses.error:
                    continue
                if event is not None:
                    before = view.top
                    view.wheel(*event)
                    events.append(["wheel", *event, before, view.top])
                elif char == "q":
                    break
                elif char in (curses.KEY_UP, curses.KEY_DOWN):
                    view.key(char)
                    events.append(["key", char, view.top])
        finally:
            mouse.enable(False)

    curses.wrapper(loop)
    print("MOUSE_RESULT=" + json.dumps(events))


@unittest.skipUnless(shutil.which("tic"), "requires the terminfo compiler")
class TerminalMouseTests(unittest.TestCase):
    def test_sgr_mouse_survives_terminfo_decoding_and_keeps_arrow_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "terminal.src"
            source.write_text("devtui-sgr|SGR regression fixture,\n"
                              "  kmous=\\E[<,\n"
                              "  XM=\\E[?1006;1000%?%p1%{1}%=%th%el%;,\n"
                              "  use=xterm-256color,\n")
            compiled = subprocess.run(["tic", "-x", "-o", str(root), str(source)], capture_output=True)
            if compiled.returncode:
                self.skipTest("xterm-256color terminfo unavailable")
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 120, 0, 0))
            original = termios.tcgetattr(slave)
            environment = dict(os.environ, TERM="devtui-sgr", TERMINFO=str(root))
            probe = "import runpy; runpy.run_path('tests/test_mouse_terminal.py')['mouse_probe']()"
            process = subprocess.Popen([sys.executable, "-c", probe],
                cwd=Path(__file__).resolve().parents[1], env=environment,
                stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            output = bytearray()

            def pump(seconds):
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    if select.select([master], [], [], .02)[0]:
                        output.extend(os.read(master, 65536))

            try:
                deadline = time.monotonic() + 5
                while b"\x1b[?1006h" not in output and time.monotonic() < deadline:
                    pump(.05)
                self.assertIn(b"\x1b[?1006h", output)
                for packet in (b"\x1b[<64;30;10M", b"\x1b[<65;30;10M",
                               b"\x1b[<66;30;10M", b"\x1b[<67;30;10M",
                               b"\x1bOA", b"\x1bOB"):
                    os.write(master, packet)
                    pump(.08)
                os.write(master, b"q")
                deadline = time.monotonic() + 5
                while process.poll() is None and time.monotonic() < deadline:
                    pump(.05)
                self.assertEqual(process.poll(), 0, output.decode(errors="replace"))
                pump(.05)
                result = output.decode(errors="replace").split("MOUSE_RESULT=")[-1].strip()
                self.assertEqual(json.loads(result), [
                    ["wheel", 0, -3, 280, 277], ["wheel", 0, 3, 277, 280],
                    ["wheel", -8, 0, 280, 280], ["wheel", 8, 0, 280, 280],
                    ["key", curses.KEY_UP, 279], ["key", curses.KEY_DOWN, 280],
                ])
                self.assertIn(b"\x1b[?1006l", output)
                restored = termios.tcgetattr(slave)
                # macOS may set the kernel's pending-retype flag after cbreak.
                for attributes in (original, restored):
                    attributes[3] &= ~getattr(termios, "PENDIN", 0)
                self.assertEqual(restored, original)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                os.close(master)
                os.close(slave)


if __name__ == "__main__":
    unittest.main()

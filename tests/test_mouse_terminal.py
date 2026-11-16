"""Exercise real curses with a modern SGR terminfo entry, including old macOS."""
import fcntl
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
            process = subprocess.Popen([sys.executable, "-m", "devtui", "--mouse-test", "curses"],
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
                summary = output.decode(errors="replace").split("devtui mouse-test mode=curses")[-1]
                self.assertIn("UP=1 DOWN=1 LEFT=1 RIGHT=1 ARROW_UP=1 ARROW_DOWN=1 OTHER=0", summary)
                self.assertIn("UP top=281->278", summary)
                self.assertIn("DOWN top=278->281", summary)
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

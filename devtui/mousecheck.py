"""Terminal-only diagnostics. Never loads projects, starts servers or saves input."""
from collections import Counter, deque
import curses
import os
import re
import select
import sys
import termios
import tty

from .config import UserError
from .logview import LogView
from .mouse import MouseInput
from .text import clean, fit


class RawReports:
    """Parse only mouse reports and cursor arrows, discarding all other input."""

    def __init__(self):
        self.pending = b""
        self.done = False

    def feed(self, data):
        self.pending += data
        events = []
        prefixes = (b"\x1b[<", b"\x1b[M", b"\x1b[A", b"\x1b[B", b"\x1b[C", b"\x1b[D",
                    b"\x1bOA", b"\x1bOB", b"\x1bOC", b"\x1bOD")
        while self.pending:
            packet = self.pending
            if packet[:1] in (b"q", b"\x03"):
                self.done = True
                self.pending = b""
                break
            if packet.startswith(b"\x1b[<"):
                match = re.match(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])", packet)
                if match:
                    raw = match[0]
                    button = int(match[1])
                    events.append(self.report(button, "SGR", repr(raw), match[4] == b"M"))
                    self.pending = packet[len(raw):]
                    continue
                if len(packet) <= 64 and re.fullmatch(rb"\x1b\[<[0-9;]*", packet):
                    break
            elif packet.startswith(b"\x1b[M"):
                if len(packet) < 6:
                    break
                events.append(self.report(packet[3] - 32, "X10", repr(packet[:6]), True))
                self.pending = packet[6:]
                continue
            elif packet[:3] in prefixes[2:]:
                direction = {65: "UP", 66: "DOWN", 67: "RIGHT", 68: "LEFT"}[packet[2]]
                events.append(("ARROW_" + direction, "KEY", repr(packet[:3])))
                self.pending = packet[3:]
                continue
            if any(prefix.startswith(packet) for prefix in prefixes):
                break
            self.pending = packet[1:]
        return events

    @staticmethod
    def report(button, protocol, packet, pressed):
        direction = ("UP", "DOWN", "LEFT", "RIGHT")[button & 3] if button & 64 else "OTHER"
        phase = "press" if pressed else "release"
        return direction, protocol, f"{protocol} button={button} {phase} {packet}"


def terminal_metadata():
    version = getattr(curses, "ncurses_version", None)
    return {
        "TERM": clean(os.environ.get("TERM", "unset"))[:120],
        "TERM_PROGRAM": clean(os.environ.get("TERM_PROGRAM", "unset"))[:120],
        "TMUX": "yes" if os.environ.get("TMUX") else "no",
        "ncurses": f"{version.major}.{version.minor}" if version else "unknown",
        "BUTTON5": "yes" if hasattr(curses, "BUTTON5_PRESSED") else "no",
    }


def diagnostic_raw():
    decoder = RawReports()
    counts, recent = Counter(), deque(maxlen=12)
    descriptor = sys.stdin.fileno()
    original = termios.tcgetattr(descriptor)
    try:
        tty.setcbreak(descriptor)
        # Match the TUI's alternate screen, including iTerm2's reporting restriction.
        sys.stdout.write("\x1b[?1049h\x1b[H\x1b[2J\x1b[?1000h\x1b[?1006h")
        print("devtui mouse-test RAW - scroll in both directions, then press q")
        print("Only mouse reports and arrows are shown. Other typed text is discarded.")
        print(" ".join(f"{k}={v}" for k, v in terminal_metadata().items()))
        sys.stdout.flush()
        while not decoder.done:
            if not select.select([descriptor], [], [], .2)[0]:
                continue
            data = os.read(descriptor, 4096)
            if not data:
                break
            for direction, protocol, description in decoder.feed(data):
                counts[direction] += 1
                recent.append(description)
                print(f"{direction:<10} {description}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(descriptor, termios.TCSADRAIN, original)
        sys.stdout.write("\x1b[?1000l\x1b[?1006l\x1b[?1049l")
        sys.stdout.flush()
    return counts, recent


def diagnostic_curses():
    counts, recent = Counter(), deque(maxlen=12)
    def loop(win):
        view = LogView("Mouse diagnostic", ("diagnostic", None), [f"Sample log line {i:04d}" for i in range(600)])
        view.follow, view.top = False, 280  # Leave room for either direction.
        mouse = MouseInput()
        win.timeout(100)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(100)
        try:
            mouse.enable(True)
            while True:
                view.title = f"Mouse test CURSES / UP={counts['UP']} DOWN={counts['DOWN']}"
                view.draw(win, lambda _: 0)
                height, width = win.getmaxyx()
                info = recent[-1] if recent else "Scroll both ways. q: finish. No DB/server access."
                try:
                    win.addstr(max(0, height - 2), 1, fit(info, max(0, width - 2)))
                    win.refresh()
                except curses.error:
                    pass
                try:
                    char, event = mouse.get_input(win)
                except curses.error:
                    continue
                if event is not None:
                    dx, dy = event
                    direction = "UP" if dy < 0 else "DOWN" if dy > 0 else "LEFT" if dx < 0 else "RIGHT" if dx > 0 else "OTHER"
                    counts[direction] += 1
                    before = view.top
                    view.wheel(dx, dy)
                    recent.append(f"{direction} top={before + 1}->{view.top + 1} {mouse.last_report}")
                elif char in ("q", "\x1b"):
                    break
                elif char in (curses.KEY_UP, curses.KEY_DOWN):
                    counts["ARROW_UP" if char == curses.KEY_UP else "ARROW_DOWN"] += 1
                    view.key(char)
        finally:
            mouse.enable(False)
    try:
        curses.wrapper(loop)
    except KeyboardInterrupt:
        pass
    return counts, recent


def run_mouse_test(mode):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise UserError("マウス診断はiTerm2等の端末から実行してください")
    counts, recent = diagnostic_raw() if mode == "raw" else diagnostic_curses()
    print(f"devtui mouse-test mode={mode}")
    print(" ".join(f"{k}={v}" for k, v in terminal_metadata().items()))
    print(" ".join(f"{key}={counts[key]}" for key in ("UP", "DOWN", "LEFT", "RIGHT", "ARROW_UP", "ARROW_DOWN", "OTHER")))
    print("Last reports:")
    for report in recent:
        print(report)
    return 0

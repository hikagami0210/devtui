import curses
import re
import sys


def sgr_scroll(sequence):
    """Decode SGR mouse reports. None means this is ordinary keyboard input."""
    match = re.fullmatch(r"\[<(\d+);(\d+);(\d+)([Mm])", sequence)
    if not match:
        return None
    button = int(match[1])
    # Consume clicks/releases too, so their numeric payload cannot become keys.
    if match[4] != "M" or not button & 64:
        return (0, 0)
    direction = button & 3
    if direction in (2, 3):
        return (-8 if direction == 2 else 8, 0)
    delta = -3 if direction == 0 else 3
    return (delta, 0) if button & 4 else (0, delta)


class MouseInput:
    """Use native mouse events, with SGR fallback for older macOS curses."""

    def __init__(self):
        self.enabled = False
        self.previous_mask = 0
        self.last_report = ""

    def enable(self, enabled):
        if enabled == self.enabled:
            return
        if enabled:
            try:
                mask = curses.BUTTON4_PRESSED | getattr(curses, "BUTTON5_PRESSED", 0)
                _, self.previous_mask = curses.mousemask(mask)
                curses.mouseinterval(0)
            except curses.error:
                self.previous_mask = 0
            # SGR supports both wheel directions even when BUTTON5 is unavailable.
            sys.stdout.write("\x1b[?1000h\x1b[?1006h")
        else:
            try:
                curses.mousemask(self.previous_mask)
            except curses.error:
                pass
            sys.stdout.write("\x1b[?1000l\x1b[?1006l")
        sys.stdout.flush()
        self.enabled = enabled

    def read(self, win, char, timeout=100):
        if char == curses.KEY_MOUSE:
            try:
                _, _, _, _, state = curses.getmouse()
            except curses.error:
                self.last_report = "curses.getmouse: no event"
                return (0, 0)
            self.last_report = f"curses bstate=0x{state:x} button5={hasattr(curses, 'BUTTON5_PRESSED')}"
            delta = -3 if state & curses.BUTTON4_PRESSED else 3 if state & getattr(curses, "BUTTON5_PRESSED", 0) else 0
            return (delta, 0) if state & curses.BUTTON_SHIFT else (0, delta)
        if char != "\x1b":
            return None
        # Old curses exposes SGR as characters. Read only this bounded sequence,
        # restoring non-mouse input and the caller's timeout on every exit path.
        pending = []
        try:
            win.timeout(20)
            for index in range(48):
                try:
                    value = win.get_wch()
                except curses.error:
                    break
                pending.append(value)
                if not isinstance(value, str):
                    break
                if (index == 0 and value != "[") or (index == 1 and value != "<"):
                    break
                if index > 1 and value in ("M", "m"):
                    event = sgr_scroll("".join(pending))
                    if event is not None:
                        self.last_report = "SGR ESC" + "".join(pending)
                        return event
                    break
                if index > 1 and value not in "0123456789;":
                    break
        finally:
            win.timeout(timeout)
        for value in reversed(pending):
            if isinstance(value, str):
                curses.unget_wch(value)
            else:
                curses.ungetch(value)
        return None

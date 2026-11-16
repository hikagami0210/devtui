from bisect import bisect_left, bisect_right
import curses
import re

from .text import clean, fit, slice_cells, text_width
from .config import HISTORY_LIMIT


class LogView:
    """A frozen snapshot while browsing, or a live tail while following."""

    def __init__(self, title, source, lines=(), loaded=True):
        self.title = title
        self.source = source
        self.lines = [clean(line) for line in lines]
        self.loaded = loaded
        self.follow = True
        self.top = 0
        self.column = 0
        self.height = 20
        self.width = 80
        self.query = ""
        self.editing = False
        self.matches = []
        self.match_index = -1
        self.message = ""
        self.set_viewport(self.height, self.width)

    @property
    def max_top(self):
        return max(0, len(self.lines) - self.height)

    def set_viewport(self, height, width):
        self.height, self.width = max(1, height), max(1, width)
        self.top = self.max_top if self.follow else min(self.top, self.max_top)

    def update(self, lines):
        if self.follow or not self.loaded:
            self.lines = [clean(line) for line in lines]
            self.loaded = True
            self.top = self.max_top if self.follow else min(self.top, self.max_top)
            self.find_matches()

    def pattern(self):
        return re.compile(re.escape(self.query), re.IGNORECASE) if self.query else None

    def find_matches(self):
        pattern = self.pattern()
        self.matches = [i for i, line in enumerate(self.lines) if pattern.search(line)] if pattern else []
        self.match_index = -1

    def scroll(self, amount):
        self.follow = False
        self.top = max(0, min(self.max_top, self.top + amount))

    def pan(self, amount):
        self.follow = False
        longest = max((text_width(line) for line in self.lines[self.top:self.top + self.height]), default=0)
        self.column = max(0, min(max(0, longest - self.width), self.column + amount))

    def wheel(self, dx, dy):
        if not self.loaded:
            return
        if dy:
            self.scroll(dy)
        if dx:
            self.pan(dx)

    def jump(self, direction=1):
        self.follow = False
        if not self.matches:
            self.message = "No matches" if self.query else "Press / to search"
            return
        if self.match_index >= 0:
            index = self.match_index + direction
        elif direction > 0:
            index = bisect_left(self.matches, self.top)
        else:
            index = bisect_right(self.matches, self.top) - 1
        self.match_index = index % len(self.matches)
        line = self.matches[self.match_index]
        self.top = max(0, min(self.max_top, line - self.height // 2))
        match = self.pattern().search(self.lines[line])
        left = text_width(self.lines[line][:match.start()])
        right = text_width(self.lines[line][:match.end()])
        if left < self.column or right > self.column + self.width:
            self.column = max(0, left - self.width // 3)
        self.message = ""

    def key(self, key, text=None):
        if self.editing:
            if key == 27:
                self.editing = False
                self.query = ""
                self.find_matches()
            elif key in (10, 13):
                self.editing = False
                self.jump()
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
                self.find_matches()
            elif (text is not None and text.isprintable()) or 32 <= key <= 126:
                self.query += text if text is not None else chr(key)
                self.find_matches()
            return None
        if key in (27, ord("q"), ord("f")):
            return "close"
        if not self.loaded:
            return None
        if key in (curses.KEY_UP, ord("k")):
            self.scroll(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self.scroll(1)
        elif key == curses.KEY_PPAGE:
            self.scroll(-max(1, self.height - 1))
        elif key == curses.KEY_NPAGE:
            self.scroll(max(1, self.height - 1))
        elif key in (curses.KEY_HOME, ord("g")):
            self.follow, self.top, self.column = False, 0, 0
        elif key in (curses.KEY_END, ord("G")):
            self.follow, self.top, self.column = True, self.max_top, 0
            return "refresh"
        elif key == ord(" "):
            self.follow = not self.follow
            if self.follow:
                self.top = self.max_top
                return "refresh"
        elif key in (curses.KEY_LEFT, ord("h")):
            self.pan(-8)
        elif key in (curses.KEY_RIGHT, ord("l")):
            self.pan(8)
        elif key == ord("/"):
            self.follow, self.editing, self.query, self.message = False, True, "", ""
            self.find_matches()
        elif key in (ord("n"), ord("N")):
            self.jump(1 if key == ord("n") else -1)
        return None

    def draw(self, win, color):
        win.erase()
        height, width = win.getmaxyx()
        def put(y, x, text, attr=0, limit=None):
            if 0 <= y < height and 0 <= x < width:
                try:
                    win.addstr(y, x, fit(text, max(0, min(width - x, limit if limit is not None else width - x))), attr)
                except curses.error:
                    pass
        if height < 10 or width < 40:
            put(0, 0, "Resize to 40 x 10. Esc: return")
            win.refresh()
            return
        digits = max(3, len(str(len(self.lines))))
        data_x = digits + 5
        self.set_viewport(height - 5, width - data_x - 2)
        border = color(1)
        put(0, 0, "┌" + "─" * (width - 2) + "┐", border)
        for y in range(1, height - 3):
            put(y, 0, "│", border)
            put(y, width - 1, "│", border)
        put(height - 3, 0, "└" + "─" * (width - 2) + "┘", border)
        badge = "LOADING" if not self.loaded else "FOLLOW" if self.follow else "PAUSED"
        put(0, 2, f" {self.title} ", color(1) | curses.A_BOLD, width - len(badge) - 8)
        put(0, width - len(badge) - 4, f" {badge} ", color(3 if self.follow else 2) | curses.A_BOLD)
        end = min(len(self.lines), self.top + self.height)
        status = f"Lines {self.top + 1 if self.lines else 0}-{end}/{len(self.lines)}  |  Column {self.column + 1}"
        if not self.loaded:
            status += f"  |  Loading up to {HISTORY_LIMIT} history lines..."
        put(1, 2, status, curses.A_DIM, width - 4)
        pattern = self.pattern()
        active = self.matches[self.match_index] if self.match_index >= 0 else None
        for y, line_number in enumerate(range(self.top, end), 2):
            line = self.lines[line_number]
            put(y, 2, f"{line_number + 1:>{digits}} │ ", color(5) | curses.A_BOLD if line_number == active else curses.A_DIM)
            put(y, data_x, slice_cells(line, self.column, self.width), limit=self.width)
            if pattern:
                for match in pattern.finditer(line):
                    start = text_width(line[:match.start()])
                    stop = text_width(line[:match.end()])
                    left, right = max(start, self.column), min(stop, self.column + self.width)
                    if left < right:
                        put(y, data_x + left - self.column, slice_cells(line, left, right - left),
                            color(8 if line_number == active else 7) | curses.A_BOLD, right - left)
        if not self.lines:
            put(2, data_x, "No stored logs. New output will appear here.", curses.A_DIM, self.width)
        if self.editing or self.query:
            counter = f"{self.match_index + 1}/{len(self.matches)} matching lines"
            prompt = "/ " + self.query + ("█" if self.editing else "")
            prompt_width = max(1, width - len(counter) - 5)
            if text_width(prompt) > prompt_width:
                prompt = slice_cells(prompt, text_width(prompt) - prompt_width, prompt_width)
            put(height - 2, 1, prompt, color(2), prompt_width)
            put(height - 2, width - len(counter) - 2, counter, color(5))
        else:
            put(height - 2, 1, self.message or "Trackpad/wheel scrolls. G or Space resumes live output.", color(2), width - 2)
        if self.message and self.query and not self.editing:
            put(1, max(2, width - len(self.message) - 3), self.message, color(2))
        keys = "↑↓/jk scroll  PgUp/Dn page  ←→ pan  / search  n/N next/prev  G tail  Esc back"
        if width < 90:
            keys = "jk scroll  PgUp/Dn page  / search  n/N next/prev  G tail  Esc back"
        if width < 68:
            keys = "jk scroll  / find  G tail  Esc back"
        if self.editing:
            keys = "Enter find  Esc clear search"
        put(height - 1, 1, keys, color(2), width - 2)
        win.refresh()

from concurrent.futures import ThreadPoolExecutor
import curses
import time

from .config import HISTORY_LIMIT, UserError, scripts
from .process import Jobs
from .text import fit, text_width
from .logview import LogView
from .mouse import MouseInput
from .clipboard import copy_text
from .i18n import translate, status_text


class UI:
    tabs = ("Servers", "Mise tasks", "Npm scripts", "Runs")

    def __init__(self, backend, policy=None, language=None):
        self.backend = backend
        self.language = language or backend.config.language
        self.policy = policy or backend.config.exit_policy
        self.jobs = Jobs()
        self.tab = 0
        self.index = [0] * 4
        self.servers, self.tasks, self.scripts = [], [], []
        self.errors = []
        self.logs = {}
        self.message = self.t("Loading...")
        self.query = ""
        self.editing = False
        self.offset = 0
        self.help = False
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.clear_future = None
        self.notice_until = 0
        self.next_poll = 0
        self.refresh = True
        self.log_target = None
        self.log_view = None
        self.color = lambda _: 0

    def t(self, template, **values):
        return translate(self.language, template, **values)

    def job_label(self, label):
        prefix, separator, name = label.partition(":")
        if prefix in self.tabs:
            return self.t(prefix) + separator + name
        if prefix == "server" and name.count(":") >= 1:
            action, name = name.split(":", 1)
            return self.t("server:{action}:{name}", action=self.t(action), name=name)
        return label

    def snapshot(self, refresh, target, log_limit=150):
        result = {}
        errors = []
        for name, call in (("servers", self.backend.servers),):
            try:
                result[name] = call()
            except (UserError, ValueError) as exc:
                errors.append(str(exc))
        if refresh:
            try:
                result["tasks"] = self.backend.tasks()
            except (UserError, ValueError) as exc:
                errors.append(str(exc))
            result["scripts"], repo_errors = scripts(self.backend.config)
            errors += repo_errors
        if target:
            try:
                result["log"] = (target, self.backend.logs(target, limit=log_limit))
            except (UserError, ValueError) as exc:
                result["log"] = (target, str(exc))
            result["log_limit"] = log_limit
        result["errors"] = errors
        return result

    def poll(self):
        self.follow_selected_server()
        if self.future and self.future.done():
            result = self.future.result()
            self.future = None
            for key in ("servers", "tasks", "scripts", "errors"):
                if key in result:
                    setattr(self, key, result[key])
            if "log" in result:
                name, value = result["log"]
                # An older snapshot may complete after the user requests a clear.
                if not self.clear_future or self.clear_future[0] != name:
                    self.logs[name] = value.splitlines()[-HISTORY_LIMIT:]
                    if (self.log_view and self.log_view.source == ("server", name)
                            and result.get("log_limit") == HISTORY_LIMIT):
                        self.log_view.update(self.logs[name])
            if time.monotonic() >= self.notice_until:
                self.message = self.errors[0] if self.errors else self.t("Ready")
            self.follow_selected_server()
        if self.clear_future and self.clear_future[1].done():
            name, future = self.clear_future
            self.clear_future = None
            try:
                future.result()
                self.logs[name] = []
                self.offset = 0
                self.message = self.t("Cleared stored logs: {name}", name=name)
            except (UserError, ValueError) as exc:
                self.message = self.t("Log clear failed: {error}", error=exc)
            self.notice_until = time.monotonic() + 4
            self.next_poll = 0
        if not self.future and not self.clear_future and time.monotonic() >= self.next_poll:
            limit = HISTORY_LIMIT if self.log_view else 150
            self.future = self.pool.submit(self.snapshot, self.refresh, self.log_target, limit)
            self.refresh = False
            self.next_poll = time.monotonic() + 2

    def follow_selected_server(self):
        if self.log_view:
            target = self.log_view.source[1] if self.log_view.source[0] == "server" and self.log_view.follow else None
        else:
            row = self.selected() if self.tab == 0 else None
            target = row[0] if row else None
        if target != self.log_target:
            self.log_target = target
            self.offset = 0
            self.next_poll = 0

    def rows(self):
        if self.tab == 0:
            rows = [(x["name"], f'{x["name"]:<24} {x.get("branch", "-")} {status_text(self.language, x.get("status", "unknown")):<14} {x.get("url") or x.get("port") or ""}', x) for x in self.servers]
        elif self.tab == 1:
            rows = [(x["name"], f'{x["name"]:<32} {x.get("description", "")}', x) for x in self.tasks]
        elif self.tab == 2:
            rows = [(f"{x.repo.name}/{x.name}", f'{x.repo.name:<18} {x.name:<25} {x.manager:<5} {x.command}', x) for x in self.scripts]
        else:
            rows = [(str(id(x)), f'{status_text(self.language, x.status):<13} {self.job_label(x.label)}', x) for x in reversed(self.jobs.items)]
        return [x for x in rows if self.query.casefold() in x[1].casefold()]

    def selected(self):
        rows = self.rows()
        self.index[self.tab] = min(max(self.index[self.tab], 0), max(len(rows) - 1, 0))
        return rows[self.index[self.tab]] if rows else None

    def start(self, label, argv, timeout=None):
        self.jobs.start(label, argv, self.backend.config.root, self.backend.env, timeout)
        self.message = self.t("Started: {label} (output in Runs)", label=self.job_label(label))
        self.offset = 0

    def action(self, key, text=None):
        if key == ord("L") and not self.editing and not (self.log_view and self.log_view.editing):
            self.language = "ja" if self.language == "en" else "en"
            self.message = self.t("Language: {language}", language="日本語" if self.language == "ja" else "English")
            self.notice_until = time.monotonic() + 4
            if self.log_view:
                self.log_view.language = self.language
                self.log_view.message = self.message
            return
        if self.log_view:
            result = self.log_view.key(key, text)
            if result == "close":
                self.log_view = None
                self.offset = 0
                self.next_poll = 0
            elif result == "refresh":
                self.next_poll = 0
            elif result in ("copy", "copy_all"):
                view = self.log_view
                lines = view.lines if result == "copy_all" else view.lines[view.top:view.top + view.height]
                if not lines:
                    view.message = self.t("No logs to copy")
                else:
                    try:
                        copy_text("\n".join(lines) + "\n")
                        scope = self.t("all loaded" if result == "copy_all" else "visible rows")
                        view.message = self.t("Copied {count} lines ({scope})", count=len(lines), scope=scope)
                    except UserError as exc:
                        view.message = self.t("Copy failed: {error}", error=exc)
            return
        if self.editing and key != 27:
            if key in (10, 13):
                self.editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.query = self.query[:-1]
            elif (text is not None and text.isprintable()) or 32 <= key <= 126:
                self.query += text if text is not None else chr(key)
            return
        row = self.selected()
        if key in (ord("1"), ord("2"), ord("3"), ord("4"), 9):
            self.tab = (self.tab + 1) % 4 if key == 9 else key - ord("1")
            self.query = ""
            self.offset = 0
        elif key in (curses.KEY_DOWN, ord("j")):
            self.index[self.tab] += 1
            self.offset = 0
        elif key in (curses.KEY_UP, ord("k")):
            self.index[self.tab] -= 1
            self.offset = 0
        elif key == curses.KEY_PPAGE:
            self.offset += 8
        elif key == curses.KEY_NPAGE:
            self.offset = max(0, self.offset - 8)
        elif key == ord("/"):
            self.editing = True
            self.query = ""
            self.index[self.tab] = 0
        elif key == 27:
            self.editing = False
            self.query = ""
            self.offset = 0
            self.help = False
        elif key == ord("?"):
            self.help = not self.help
        elif key == ord("e"):
            self.policy = "stop" if self.policy == "keep" else "keep"
        elif key == ord("g"):
            self.refresh = True
            self.next_poll = 0
        elif row and self.tab == 0 and key in (ord("s"), ord("x"), ord("r")):
            if any(not j.done.is_set() and j.label.startswith("server:") for j in self.jobs.items):
                raise UserError(self.t("A server operation is running. Wait for it to finish."))
            action = {ord("s"): "start", ord("x"): "stop", ord("r"): "restart"}[key]
            self.start(f"server:{action}:{row[0]}", self.backend.daemon_command(action, row[0]), 120)
            self.log_target = row[0]
        elif row and self.tab in (0, 3) and key in (ord("f"), 10, 13):
            if self.tab == 0:
                self.log_view = LogView(f"{self.t('Logs')} / {row[0]}", ("server", row[0]), self.logs.get(row[0], ()), loaded=False, language=self.language)
                self.log_target = row[0]
            else:
                job = row[2]
                self.log_view = LogView(f"{self.t('Output')} / {self.job_label(job.label)}", ("job", job), job.output(), language=self.language)
            self.next_poll = 0
        elif row and self.tab == 0 and key == ord("l"):
            self.log_target = row[0]
            self.next_poll = 0
        elif row and self.tab == 0 and key == ord("C"):
            if self.clear_future:
                raise UserError(self.t("Log clear is already running."))
            name = row[0]
            # Use the same worker as log reads so a clear cannot race a new read.
            self.clear_future = (name, self.pool.submit(self.backend.clear_logs, name))
            self.log_target = name
            self.message = self.t("Clearing stored logs: {name}...", name=name)
            self.notice_until = time.monotonic() + 12
        elif row and key in (10, 13) and self.tab in (1, 2):
            if self.tab == 1 and (row[2].get("raw") or row[2].get("interactive")):
                raise UserError(self.t("Run interactive tasks in a terminal: mise run <task>"))
            command = self.backend.command("run", row[0]) if self.tab == 1 else self.backend.script_command(row[2])
            self.start(f"{self.tabs[self.tab]}:{row[0]}", command)
            self.tab = 3
            self.index[3] = 0
            self.query = ""
        elif row and self.tab == 3 and key == ord("c"):
            row[2].cancel()

    def draw(self, win):
        if self.log_view:
            if self.log_view.source[0] == "job":
                job = self.log_view.source[1]
                self.log_view.title = f"{self.t('Output')} / {self.job_label(job.label)} / {status_text(self.language, job.status)}"
                self.log_view.update(job.output())
            else:
                self.log_view.title = f"{self.t('Logs')} / {self.log_view.source[1]}"
            self.log_view.language = self.language
            self.log_view.draw(win, self.color)
            return
        win.erase()
        height, width = win.getmaxyx()
        def put(y, x, text, attr=0, limit=None):
            if 0 <= y < height and 0 <= x < width:
                try:
                    win.addstr(y, x, fit(text, max(0, min(width - x, limit if limit is not None else width - x))), attr)
                except curses.error:
                    pass

        def box(y, size, title, badge="", active=False):
            border = self.color(1) | (curses.A_BOLD if active else curses.A_DIM)
            put(y, 0, "┌" + "─" * (width - 2) + "┐", border)
            for line in range(y + 1, y + size - 1):
                put(line, 0, "│", border)
                put(line, width - 1, "│", border)
            put(y + size - 1, 0, "└" + "─" * (width - 2) + "┘", border)
            put(y, 2, f" {title} ", self.color(1) | curses.A_BOLD, width - text_width(badge) - 8)
            if badge:
                put(y, width - text_width(badge) - 4, f" {badge} ", self.color(5) | curses.A_BOLD)

        def state_style(state):
            if state in ("running", "exit 0"):
                return self.color(3)
            if state in ("stopped", "available", "cancelled"):
                return curses.A_DIM
            if state in ("starting", "stopping", "cancelling"):
                return self.color(2)
            return self.color(4)

        if height < 16 or width < 68:
            put(0, 0, self.t("Resize terminal to at least 68 x 16. q: quit"))
            win.refresh()
            return

        active_runs = sum(not j.done.is_set() for j in self.jobs.items)
        running = sum(s.get("status") == "running" for s in self.servers)
        box(0, 5, self.t("WORKSPACE"), "DEVTUI / v0.2", active=True)
        context_width = width - 43 if width >= 110 else width - 5
        policy = self.t(f"EXIT {self.policy.upper()}")
        put(1, 2, self.backend.config.root.name, self.color(1) | curses.A_BOLD, context_width - text_width(policy) - 3)
        put(1, context_width - text_width(policy) + 2, policy, self.color(2 if self.policy == "stop" else 3) | curses.A_BOLD)
        put(2, 2, str(self.backend.config.root), curses.A_DIM, context_width)
        put(3, 2, self.t("Repos: {repos}   Servers: {running}/{total}   Runs: {runs}", repos=len(self.backend.config.repos), running=running, total=len(self.backend.config.daemons), runs=active_runs), limit=context_width)
        if width >= 110:
            divider = width - 40
            for y in (1, 2, 3):
                put(y, divider, "│", self.color(1) | curses.A_DIM)
            hints = (("<s> start    <x> stop    <r> restart", "<enter> logs  <C> clear  <g> refresh", "<e> exit policy         <?> help"),
                     ("<enter> execute task", "<g> refresh task catalog", "<e> exit policy         <?> help"),
                     ("<enter> execute script", "<g> reread package.json", "<e> exit policy         <?> help"),
                     ("<enter> full-screen output", "<c> cancel selected run", "<e> exit policy         <?> help"))[self.tab]
            for i, hint in enumerate(hints, 1):
                put(i, divider + 2, self.t(hint), self.color(2), width - divider - 4)

        tab_x = 1
        for i, name in enumerate(self.tabs):
            label = f" {i + 1} {self.t(name)} "
            put(5, tab_x, label, (self.color(6) | curses.A_BOLD) if i == self.tab else curses.A_DIM)
            tab_x += text_width(label) + 2
        filter_x = 2
        put(6, filter_x, "/ " + (self.query or ("" if self.editing else self.t("filter..."))) + ("█" if self.editing else ""), self.color(2) if self.editing else curses.A_DIM, width - filter_x - 2)

        row = self.selected()
        rows = self.rows()
        available = height - 9
        list_height = max(4, int(available * 0.45))
        count = f"{self.index[self.tab] + 1 if rows else 0}/{len(rows)}"
        box(7, list_height, f"{self.t(self.tabs[self.tab])} [{len(rows)}]", count, active=True)
        table_width = width - 6
        if self.tab == 0:
            name_width = min(24, table_width // 4)
            columns = [("NAME", name_width), ("STATE", 12), ("PORT", 8), ("BRANCH", table_width - name_width - 20)]
        elif self.tab == 1:
            name_width = max(18, table_width // 3)
            columns = [("NAME", name_width), ("MODE", 13), ("DESCRIPTION", table_width - name_width - 13)]
        elif self.tab == 2:
            repo_width, name_width = min(18, table_width // 5), min(25, table_width // 4)
            columns = [("REPO", repo_width), ("SCRIPT", name_width), ("PM", 7), ("COMMAND", table_width - repo_width - name_width - 7)]
        else:
            columns = [("STATE", 15), ("TASK", table_width - 15)]
        x = 3
        for title, size in columns:
            right = self.tab == 0 and title == "BRANCH"
            label = self.t(title)
            put(8, x + (size - 1 - text_width(label) if right else 0), label, self.color(5) | curses.A_BOLD, size - 1)
            x += size
        visible = list_height - 3
        start = max(0, self.index[self.tab] - visible + 1)
        for i, item in enumerate(rows[start:start + visible], start):
            y = 9 + i - start
            selected = i == self.index[self.tab]
            style = self.color(6) | curses.A_BOLD if selected else 0
            if selected:
                put(y, 1, " " * (width - 2), style)
                put(y, 1, "▸", style)
            value = item[2]
            if self.tab == 0:
                state = value.get("status", "unknown")
                cells = (item[0], status_text(self.language, state), str(value.get("port") or "-"), value.get("branch", "-"))
            elif self.tab == 1:
                state = None
                cells = (item[0], self.t("interactive" if value.get("raw") or value.get("interactive") else "task"), value.get("description", ""))
            elif self.tab == 2:
                state = None
                cells = (value.repo.name, value.name, value.manager, value.command)
            else:
                state = value.status
                cells = (status_text(self.language, state), self.job_label(value.label))
            x = 3
            for column, (cell, (_, size)) in enumerate(zip(cells, columns)):
                cell_style = style
                if not selected and state and column == (1 if self.tab == 0 else 0):
                    cell_style = state_style(state)
                text = fit(cell, size - 1)
                # Align by terminal cells, including wide characters in branch names.
                cells_used = text_width(text)
                right = self.tab == 0 and columns[column][0] == "BRANCH"
                put(y, x + (size - 1 - cells_used if right else 0), text, cell_style, size - 1)
                x += size
        if not rows:
            put(9, 3, self.t("No entries. <g> refresh catalog / <esc> clear filter"), curses.A_DIM, table_width)

        split = 7 + list_height
        detail_title = self.t("Details")
        detail_badge = ""
        detail = []
        if row:
            if self.tab == 0:
                detail_title = f"{self.t('Logs')} / {row[0]}"
                detail_badge = self.t("HISTORY + AUTO / 2s")
                detail = self.logs.get(row[0], [self.t("Loading server logs...")])
                if not detail:
                    detail = [self.t("No stored logs. New output will appear here.")]
            elif self.tab == 1:
                detail_title = f"{self.t('Task')} / {row[0]}"
                detail = [f"mise run {row[0]}", str(row[2].get("description", "")), self.t("Enter: run (non-interactive); output appears in Runs")]
            elif self.tab == 2:
                detail_title = f"{self.t('Script')} / {row[0]}"
                detail = [str(row[2].repo.path), f"{row[2].manager} run {row[2].name}", row[2].command, self.t("Enter: run; g: reread package.json")]
            else:
                detail_title = f"{self.t('Output')} / {self.job_label(row[2].label)}"
                detail_badge = status_text(self.language, row[2].status)
                detail = row[2].output()
        if self.help:
            detail_title = self.t("KEYS / MVP")
            detail_badge = self.t("<?> close")
            detail = [self.t(line) for line in ("1-4 / Tab: switch view   j/k / arrows: select   /: filter   Esc: clear", "Servers: s start / x stop / r restart / C clear logs (auto follow)", "Servers/Runs: Enter or f opens full-screen logs; Esc returns", "Tasks: Enter run   Runs: c cancel   PgUp/PgDn: scroll   g: refresh", "e: toggle exit KEEP/STOP   q/Ctrl-C: quit   ?: help   L: language", "Full-screen: / search, n/N next/prev, G tail, Space pause/resume", "Full-screen: c copy visible rows, Option+c/C copy all loaded logs")]
        detail_height = available - list_height
        box(split, detail_height, detail_title, detail_badge)
        space = detail_height - 2
        end = max(0, len(detail) - self.offset)
        for i, line in enumerate(detail[max(0, end - space):end]):
            put(split + 1 + i, 2, line, limit=width - 4)
        if self.offset:
            position = self.t(" {count} lines above tail ", count=self.offset)
            put(height - 3, width - text_width(position) - 3, position, self.color(2))
        put(height - 2, 1, ("! " if self.errors else "● ") + self.message, self.color(2) if self.errors else self.color(3), width - 2)
        key_x = 1
        view_shortcut = ("f", "logs") if self.tab in (0, 3) else ("g", "refresh")
        action_shortcut = ("C", "clear") if self.tab == 0 else ("c", "cancel") if self.tab == 3 else ("e", "exit")
        shortcuts = (("1-4", "views"), ("/", "filter"), view_shortcut, action_shortcut, ("L", "lang"), ("?", "help"), ("q", "quit"))
        if width < 90:
            shortcuts = (("1-4", "views"), ("/", "filter"), ("L", "lang"), ("q", "quit"))
        for key, label in shortcuts:
            label = self.t(label)
            put(height - 1, key_x, f"<{key}>", self.color(2) | curses.A_BOLD)
            key_x += len(key) + 2
            put(height - 1, key_x, f" {label}  ", curses.A_DIM)
            key_x += text_width(label) + 3
        win.refresh()

    def loop(self, win):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)
            curses.init_pair(2, curses.COLOR_YELLOW, -1)
            curses.init_pair(3, curses.COLOR_GREEN, -1)
            curses.init_pair(4, curses.COLOR_RED, -1)
            curses.init_pair(5, curses.COLOR_MAGENTA, -1)
            curses.init_pair(6, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_YELLOW)
            curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_MAGENTA)
            self.color = curses.color_pair
        win.timeout(100)
        if hasattr(curses, "set_escdelay"):
            curses.set_escdelay(100)
        mouse = MouseInput()
        try:
            while True:
                self.poll()
                self.draw(win)
                mouse.enable(self.log_view is not None)
                try:
                    char, event = mouse.get_input(win)
                except curses.error:
                    char, event = -1, None
                if event is not None:
                    if self.log_view:
                        self.log_view.wheel(*event)
                    continue
                key = (ord(char) if ord(char) < 256 else -2) if isinstance(char, str) else char
                if key in (ord("q"), 3) and not self.editing and not self.log_view:
                    return
                try:
                    self.action(key, char if isinstance(char, str) else None)
                except UserError as exc:
                    self.message = str(exc)
        finally:
            mouse.enable(False)

    def run(self):
        try:
            curses.wrapper(self.loop)
        except KeyboardInterrupt:
            pass
        except curses.error as exc:
            raise UserError(f"端末を初期化できません: {exc}。TERM設定を確認してください。") from exc
        finally:
            print(self.t("Stopping active tasks..."))
            self.jobs.close()
            self.pool.shutdown(wait=True)
            if self.clear_future:
                name, future = self.clear_future
                try:
                    future.result()
                    print(self.t("Cleared stored logs: {name}", name=name))
                except (UserError, ValueError) as exc:
                    print(self.t("Log clear failed: {error}", error=exc))
            if self.policy == "stop":
                print(self.t("Stopping daemons declared in this mise.toml..."))
                self.backend.stop_all()
            else:
                print(self.t("Daemons kept running. Stop them with: mise daemons stop <name>"))

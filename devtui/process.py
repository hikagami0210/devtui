from collections import deque
from dataclasses import dataclass, field
import os
import signal
import subprocess
import threading
import time

from .config import UserError


def terminate(process):
    # Always target the session, including children left after the leader exits.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        # Restricted execution environments can disallow group signals.
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
        return
    time.sleep(0.3)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # macOS can return EPERM for an already-dead group before wait() reaps it.
        if process.poll() is None:
            process.kill()


def capture(argv, cwd, timeout=12, env=None):
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            terminate(process)
            process.communicate()
            raise UserError(f"コマンドがタイムアウトしました: {argv[0]}")
        if process.returncode:
            message = (stderr or stdout).decode(errors="replace")[-6000:]
            raise UserError(message.strip() or f"exit {process.returncode}")
        return stdout.decode(errors="replace")
    except OSError as exc:
        raise UserError(str(exc)) from exc


@dataclass
class Job:
    label: str
    argv: list[str]
    cwd: object
    env: dict
    timeout: float | None = None
    lines: deque = field(default_factory=lambda: deque(maxlen=1500))
    code: int | None = None
    process: object = None
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def append(self, text):
        with self.lock:
            self.lines.extend(text.splitlines() or [text])

    def output(self):
        with self.lock:
            return list(self.lines)

    @property
    def status(self):
        if not self.done.is_set():
            return "cancelling" if self.cancelled else "running"
        return "cancelled" if self.cancelled else f"exit {self.code}"

    def run(self):
        try:
            with self.lock:
                if self.cancelled:
                    self.code = -15
                    return
                self.process = subprocess.Popen(self.argv, cwd=self.cwd, env=self.env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    start_new_session=True)
            def read():
                import codecs
                decode = codecs.getincrementaldecoder("utf-8")("replace")
                pending = ""
                while chunk := self.process.stdout.read1(4096):
                    pending += decode.decode(chunk).replace("\r", "\n")
                    while "\n" in pending:
                        line, pending = pending.split("\n", 1)
                        self.append(line)
                    if len(pending) > 8192:
                        self.append(pending[:8192])
                        pending = pending[8192:]
                self.append(pending + decode.decode(b"", final=True))
            reader = threading.Thread(target=read, daemon=True)
            reader.start()
            try:
                self.code = self.process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                self.append("[timeout]")
                terminate(self.process)
                self.code = self.process.wait()
            # A background descendant must not hold the pipe or escape task ownership.
            reader.join(0.3)
            if reader.is_alive():
                terminate(self.process)
                reader.join(2)
            self.process.stdout.close()
        except OSError as exc:
            self.append(str(exc))
            self.code = 127
        finally:
            self.done.set()

    def cancel(self):
        with self.lock:
            if self.done.is_set():
                return
            self.cancelled = True
            process = self.process
        if process:
            terminate(process)


class Jobs:
    def __init__(self):
        self.items = []

    def start(self, label, argv, cwd, env, timeout=None):
        if any(j.label == label and not j.done.is_set() for j in self.items):
            raise UserError("同じ操作が実行中です")
        job = Job(label, argv, cwd, env, timeout)
        self.items.append(job)
        # Keep session history bounded; active jobs are retained.
        finished = [j for j in self.items if j.done.is_set()]
        for old in finished[:-99]:
            self.items.remove(old)
        threading.Thread(target=job.run, daemon=True).start()
        return job

    def close(self):
        for job in self.items:
            if not job.done.is_set():
                job.cancel()
        for job in self.items:
            job.done.wait(5)

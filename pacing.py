"""App-wide sending pace, shared by every agent process on this computer.

Several campaigns (one after another, or in parallel browser profiles) must
never multiply the sending speed. Before each recipient an agent takes the
"send turn" from a small file; when the recipient is done it hands the turn
back with "next send not before now + delay". So sends from all campaigns are
one at a time with the delay between them, and the daily cap can be checked
exactly while the turn is held (history is written before the turn is given back).

A turn held by a process that died is freed (dead PID, or after LEASE seconds).
No third-party imports.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

LEASE_SECONDS = 900          # a send never takes this long; frees a hung holder
LOCK_STALE_SECONDS = 30


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class Pace:
    def __init__(self, path, pid: int | None = None, clock=time.time, sleep=time.sleep, alive=pid_alive):
        self.path = Path(path)
        self.lock = self.path.with_name(self.path.name + ".lock")
        self.pid = pid or os.getpid()
        self.clock, self.sleep, self.alive = clock, sleep, alive
        self.held = False

    # ------------------------------------------------------------ file lock (short)
    def _locked(self, fn):
        for _ in range(400):
            try:
                fd = os.open(str(self.lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    if self.clock() - self.lock.stat().st_mtime > LOCK_STALE_SECONDS:
                        self.lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                time.sleep(0.05)
                continue
            try:
                os.close(fd)
                return fn(self._read())
            finally:
                self.lock.unlink(missing_ok=True)
        raise RuntimeError(f"Could not lock {self.lock}")

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _write(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------ turn
    def try_acquire(self) -> float:
        """Take the turn if free: returns 0. Otherwise seconds to wait (> 0)."""
        def take(state):
            now = self.clock()
            holder = int(state.get("pid") or 0)
            busy = holder and holder != self.pid and state.get("until", 0) > now and self.alive(holder)
            if busy:
                return max(0.5, min(5.0, state["until"] - now))
            wait = float(state.get("next_at", 0)) - now
            if wait > 0 and holder != self.pid:
                return wait
            if wait > 0 and holder == self.pid:
                return wait
            state.update({"pid": self.pid, "until": now + LEASE_SECONDS})
            self._write(state)
            return 0.0
        wait = self._locked(take)
        self.held = wait == 0
        return wait

    def acquire(self, stop=lambda: False, on_wait=None) -> bool:
        """Block until this process has the turn. False if a stop was requested."""
        told = False
        while not stop():
            wait = self.try_acquire()
            if wait <= 0:
                return True
            if on_wait and not told:
                on_wait(wait)
                told = True
            self.sleep(min(wait, 0.5))
        return False

    def release(self, delay: float = 0.0) -> None:
        """Give the turn back; the next send (any campaign) waits `delay` seconds."""
        if not self.held:
            return

        def give(state):
            if int(state.get("pid") or 0) == self.pid:
                state.update({"pid": 0, "until": 0, "next_at": self.clock() + max(0.0, float(delay))})
                self._write(state)
        self._locked(give)
        self.held = False

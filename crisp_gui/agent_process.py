"""Runs crisp_email_agent.py as a child process and tracks its state."""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from . import run_state as rs


class AgentRunner(QObject):
    line = Signal(str)            # one complete output line
    state_changed = Signal(object)  # RunState
    finished = Signal(object)     # final RunState

    def __init__(self, script: Path, parent=None):
        super().__init__(parent)
        self.script = Path(script)
        self.parser: rs.RunParser | None = None
        self.stop_file: Path | None = None
        self._killed = False
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.errorOccurred.connect(self._error)

    # ---------------------------------------------------------------- state
    @property
    def state(self) -> rs.RunState | None:
        return self.parser.state if self.parser else None

    def is_running(self) -> bool:
        return self.proc.state() != QProcess.NotRunning

    # ---------------------------------------------------------------- start
    def start(self, settings: dict, dry_run: bool, profile_dir: Path) -> str | None:
        """Start the agent. Returns an error message, or None on success."""
        if self.is_running():
            return "The automation is already running."
        if not self.script.is_file():
            return f"Agent script not found: {self.script}"

        self.stop_file = Path(tempfile.gettempdir()) / f"crisp_agent_stop_{uuid.uuid4().hex}"
        self._remove_stop_file()
        self._killed = False
        self.parser = rs.RunParser(dry_run=dry_run)

        env = QProcessEnvironment.systemEnvironment()
        values = {
            "CRISP_GUI_MODE": "1",
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "CRISP_CSV_FILE": settings["csv"],
            "CRISP_SUBJECT": settings["subject"],
            "CRISP_SEGMENT": settings["segment"],
            "CRISP_REFERENCE_FILTER": settings["reference"],
            "CRISP_SHORTCUT": settings["shortcut"],
            # The ONLY switch that controls real sending in the agent.
            "CRISP_DRY_RUN": "true" if dry_run else "false",
            "CRISP_PROFILE_DIR": str(profile_dir),
            "CRISP_STOP_FILE": str(self.stop_file),
        }
        for key, value in values.items():
            env.insert(key, str(value))

        self.proc.setProcessEnvironment(env)
        self.proc.setWorkingDirectory(str(self.script.parent))
        self.proc.setProgram(sys.executable)
        self.proc.setArguments(["-u", str(self.script)])
        self.proc.start()
        if not self.proc.waitForStarted(8000):
            err = self.proc.errorString()
            self.parser = None
            return f"Could not start the automation process: {err}"
        # The agent never needs input from us; closing stdin guarantees any
        # stray input() call fails instead of waiting forever.
        self.proc.closeWriteChannel()
        self.state_changed.emit(self.parser.state)
        return None

    # ----------------------------------------------------------------- stop
    def can_stop_gracefully(self) -> bool:
        """True when a recipient may be mid-flight, so we should not kill."""
        s = self.state
        return bool(s and s.phase in (rs.RUNNING, rs.STOPPING) and s.position > 0)

    def request_stop(self) -> str:
        """Graceful stop. Returns 'graceful' or 'killed'."""
        if not self.is_running() or not self.parser:
            return "idle"
        self.parser.request_stop()
        if self.can_stop_gracefully():
            try:
                self.stop_file.write_text("stop", encoding="utf-8")
            except OSError:
                self.force_stop()
                return "killed"
            self.state_changed.emit(self.parser.state)
            return "graceful"
        # Still starting or waiting for login: nothing is being sent.
        self.force_stop()
        return "killed"

    def force_stop(self) -> None:
        if not self.is_running():
            return
        if self.parser:
            self.parser.request_stop()
        self._killed = True
        self.proc.kill()

    # ------------------------------------------------------------- internals
    def _read(self):
        if not self.parser:
            self.proc.readAllStandardOutput()
            return
        data = bytes(self.proc.readAllStandardOutput())
        for text in self.parser.feed(data):
            self.line.emit(text)
        self.state_changed.emit(self.parser.state)

    def _error(self, error):
        if error == QProcess.FailedToStart and self.parser:
            self.parser.state.last_error = f"Failed to start: {self.proc.errorString()}"

    def _finished(self, exit_code, exit_status):
        if not self.parser:
            return
        self._read()
        for text in self.parser.flush():
            self.line.emit(text)
        crashed = exit_status == QProcess.CrashExit
        code = exit_code if not crashed else -1
        state = self.parser.finish(code, killed=self._killed)
        if crashed and not self._killed and not state.last_error:
            state.phase = rs.FAILED
            state.last_error = "The automation process crashed."
        self._remove_stop_file()
        self.state_changed.emit(state)
        self.finished.emit(state)

    def _remove_stop_file(self):
        if self.stop_file:
            try:
                os.remove(self.stop_file)
            except OSError:
                pass

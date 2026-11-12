"""Runs the campaign queue: one campaign at a time by default, or up to N at
once (each in its OWN browser profile and agent process) when parallel mode
is on. The app-wide pace file keeps all of them at one shared speed.

Slot 0 uses the main window's runner (so the Activity page shows it); extra
slots get their own AgentRunner and write their output to their run folder.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from . import campaign_store as cs
from . import run_state as rs
from .agent_process import AgentRunner


def outcome_of(state: rs.RunState) -> tuple[str, str]:
    """(campaign status, note) for a finished agent run."""
    if state.unconfirmed_send:
        return cs.UNCONFIRMED, "An email may have been sent without confirmation. Check Crisp."
    if state.phase == rs.STOPPED:
        return cs.STOPPED, "Stopped" + (f": {state.step}" if state.step else ".")
    if state.phase == rs.FAILED:
        return cs.FAILED, state.last_error or "The run failed."
    if state.no_eligible:
        return cs.FINISHED, "Nothing left to send."
    return cs.FINISHED, ""


def last_numbers(state: rs.RunState) -> dict:
    return {"sent": state.sent_confirmed if not state.dry_run else 0, "completed": state.completed,
            "failed": state.failed, "skipped": state.skipped, "elsewhere": state.elsewhere,
            "eligible": state.eligible, "dry_run": state.dry_run}


class QueueController(QObject):
    changed = Signal()          # store or slots changed: refresh the Campaigns page
    message = Signal(str)       # a line for the log
    halted = Signal(str)        # the queue stopped because of a failure

    def __init__(self, store: cs.CampaignStore, launch, main_runner: AgentRunner, script: Path,
                 max_parallel=lambda: 1, parent=None):
        """launch(campaign, runner, slot, dry) -> error text or None."""
        super().__init__(parent)
        self.store = store
        self.launch = launch
        self.main_runner = main_runner
        self.script = Path(script)
        self.max_parallel = max_parallel
        self.active = False
        self.dry = True
        self.only = ""             # a scheduled start runs just this campaign
        self.slots: dict[int, tuple[str, AgentRunner]] = {}
        self._extra: dict[int, AgentRunner] = {}

    # ---------------------------------------------------------------- state
    def slot_of(self, cid: str):
        return next((k for k, (c, _r) in self.slots.items() if c == cid), None)

    def runner_of(self, cid: str) -> AgentRunner | None:
        k = self.slot_of(cid)
        return self.slots[k][1] if k is not None else None

    def busy(self) -> bool:
        return bool(self.slots)

    # ---------------------------------------------------------------- queue
    def start(self, dry: bool, only: str = "") -> None:
        self.active, self.dry, self.only = True, dry, only
        self.message.emit(("Scheduled campaign started" if only else "Queue started")
                          + f" ({'DRY RUN' if dry else 'REAL SENDING'}).")
        self.fill()

    def stop_queue(self) -> None:
        """No new campaign starts; running ones continue until they end or are stopped."""
        if self.active:
            self.active = False
            self.message.emit("Queue stopped: no further campaign will start.")
            self.changed.emit()

    def _free_slot(self):
        for k in range(max(1, int(self.max_parallel() or 1))):
            if k in self.slots:
                continue
            if k == 0 and self.main_runner.is_running():
                continue                      # a manual run or check uses the main browser
            return k
        return None

    def fill(self) -> None:
        while self.active:
            if self.only:
                c = self.store.get(self.only)
                nxt = c if c is not None and c.status == cs.QUEUED else None
            else:
                nxt = self.store.next_queued()
            if nxt is None:
                if not self.slots:
                    self.active = False
                    self.message.emit("Queue finished.")
                break
            k = self._free_slot()
            if k is None:
                break
            runner = self.main_runner if k == 0 else self._extra_runner(k)
            err = self.launch(nxt, runner, k, self.dry)
            if err:
                self.store.finish(nxt.id, cs.FAILED, err)
                self._halt(f"'{nxt.name}' could not start: {err}")
                break
            self.slots[k] = (nxt.id, runner)
            self.store.start(nxt.id, getattr(runner, "run_dir", "") or "")
            self.message.emit(f"Campaign '{nxt.name}' started" + (f" (browser {k + 1})" if k else "") + ".")
        self.changed.emit()

    def _extra_runner(self, k: int) -> AgentRunner:
        if k not in self._extra:
            r = AgentRunner(self.script, self)
            r.finished.connect(lambda state, r=r: self.runner_finished(r, state))
            r.line.connect(lambda text, r=r: self._log_line(r, text))
            r.state_changed.connect(lambda _s: self.changed.emit())
            self._extra[k] = r
        return self._extra[k]

    @staticmethod
    def _log_line(runner, text: str) -> None:
        log = getattr(runner, "run_log", None)
        if log is not None:
            try:
                log.write(text + "\n")
                log.flush()
            except OSError:
                pass

    def runner_finished(self, runner: AgentRunner, state: rs.RunState) -> None:
        k = next((k for k, (_c, r) in self.slots.items() if r is runner), None)
        if k is None:
            return
        cid, _r = self.slots.pop(k)
        log = getattr(runner, "run_log", None)
        if log is not None:
            log.close()
            runner.run_log = None
        status, note = outcome_of(state)
        c = self.store.get(cid)
        self.store.finish(cid, status, note, last_numbers(state))
        name = c.name if c else cid
        self.message.emit(f"Campaign '{name}': {cs.STATUS_LABELS[status].lower()}"
                          f" (completed {state.completed}, failed {state.failed}).")
        if status in (cs.FAILED, cs.UNCONFIRMED):
            self._halt(f"'{name}' ended with a problem, so the queue stopped. {note}")
        else:
            QTimer.singleShot(0, self.fill)    # the shared pace file spaces the next send
        self.changed.emit()

    def _halt(self, text: str) -> None:
        self.active = False
        self.message.emit(text)
        self.halted.emit(text)

    # ---------------------------------------------------------------- per campaign
    def pause(self, cid: str) -> bool:
        """Running: pause between two recipients. Queued: skip until resumed."""
        r = self.runner_of(cid)
        if r is not None:
            if r.pause():
                self.store.campaigns[cid].status = cs.PAUSED
                self.store.save()
                self.changed.emit()
                return True
            return False
        self.store.pause(cid)
        self.changed.emit()
        return True

    def resume(self, cid: str) -> None:
        r = self.runner_of(cid)
        if r is not None:
            r.resume()
            self.store.campaigns[cid].status = cs.RUNNING
            self.store.save()
        else:
            self.store.resume(cid)
            if self.active:
                QTimer.singleShot(0, self.fill)
        self.changed.emit()

    def stop(self, cid: str) -> str:
        """Graceful stop of ONE campaign (between recipients). Others are not touched."""
        r = self.runner_of(cid)
        if r is None:
            return "idle"
        r.resume()                    # a paused campaign must wake up to see the stop
        return r.request_stop()

    def close_all(self) -> None:
        """App closing: paused campaigns stay paused; running ones become unconfirmed
        when a recipient may be in flight, otherwise stopped."""
        for k, (cid, r) in list(self.slots.items()):
            st = r.state
            c = self.store.get(cid)
            if c is not None:
                if st is not None and (st.paused or c.status == cs.PAUSED):
                    status, note = cs.PAUSED, "The app was closed while paused. Resume to continue."
                elif st is not None and st.position > st.processed and not st.dry_run:
                    status, note = cs.UNCONFIRMED, "The app was closed during a send. Check Crisp."
                else:
                    status, note = cs.STOPPED, "The app was closed."
                self.store.finish(cid, status, note, last_numbers(st) if st else None)
                if status == cs.PAUSED and cid not in self.store.queue:
                    self.store.queue.insert(0, cid)
                    self.store.save()
            if r is not self.main_runner:
                r.force_stop()
                r.proc.waitForFinished(5000)
        self.slots.clear()
        self.active = False

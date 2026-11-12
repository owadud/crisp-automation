"""Phase 2: campaigns as objects, the queue, app-wide pacing, restore after restart."""

import csv
import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, Signal  # noqa: E402

import pacing  # noqa: E402
import send_control as sc  # noqa: E402
from crisp_gui import campaign_store as cs  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.queue_controller import QueueController  # noqa: E402

BASE = {"source": "csv", "csv": "list.csv", "subject": "Hi", "shortcut": "!a", "segment": "s",
        "reference": "R", "delay": "30", "daily_cap": "100", "random_delay": "false", "data_dir": "x",
        "recent_days": "0"}


def store(tmp_path):
    return cs.CampaignStore(tmp_path / "campaigns.json")


# ------------------------------------------------------------------ campaign objects
def test_campaigns_are_saved_duplicated_and_never_keep_app_wide_pace(tmp_path):
    st = store(tmp_path)
    a = st.add("Spring", BASE)
    assert a.status == cs.READY and "delay" not in a.settings and "daily_cap" not in a.settings
    assert a.settings["campaign"] == "Spring"
    b = st.duplicate(a.id)
    assert b.name == "Spring copy" and b.settings["subject"] == "Hi" and b.id != a.id
    assert st.add("Spring", BASE).name == "Spring (2)"                    # names stay unique
    d = st.add("Draft", dict(BASE, subject=""))
    assert d.status == cs.DRAFT and d.missing() == ["subject"]
    with pytest.raises(ValueError, match="missing: subject"):
        st.enqueue(d.id)
    st.update(d.id, dict(BASE, subject="Now set"))
    assert st.get(d.id).status == cs.READY
    again = store(tmp_path)                                                # saved on disk
    assert [c.name for c in again.list()] == ["Draft", "Spring", "Spring (2)", "Spring copy"]


def test_queue_order_move_run_next_pause_and_remove(tmp_path):
    st = store(tmp_path)
    a, b, c = (st.add(n, BASE) for n in ("A", "B", "C"))
    for x in (a, b, c):
        st.enqueue(x.id)
    assert [x.name for x in st.list()][:3] == ["A", "B", "C"]
    st.move(c.id, -1)
    assert st.queue == [a.id, c.id, b.id]
    st.run_next(b.id)
    assert st.queue == [b.id, a.id, c.id]
    st.pause(b.id)
    assert st.next_queued().id == a.id                                     # paused is skipped
    st.resume(b.id)
    assert st.next_queued().id == b.id
    st.dequeue(c.id)
    assert c.id not in st.queue and st.get(c.id).status == cs.READY


def test_restart_restores_queue_and_never_resumes_a_mid_send_campaign(tmp_path):
    st = store(tmp_path)
    a, b, c = (st.add(n, BASE) for n in ("A", "B", "C"))
    for x in (a, b, c):
        st.enqueue(x.id)
    st.start(a.id)                    # running when the app died
    st.pause(c.id)
    again = store(tmp_path)
    notes = again.restore()
    assert again.get(a.id).status == cs.UNCONFIRMED and a.id not in again.queue
    assert again.get(b.id).status == cs.QUEUED and again.get(c.id).status == cs.PAUSED
    assert any("check Crisp" in n for n in notes) and any("Nothing starts" in n for n in notes)
    with pytest.raises(ValueError, match="unconfirmed"):
        again.enqueue(a.id)
    again.mark_checked(a.id)
    again.enqueue(a.id)
    assert again.get(a.id).status == cs.QUEUED


def test_queue_summary_counts_overlaps_once_and_estimates_time(tmp_path):
    st = store(tmp_path)
    a, b = st.add("A", BASE), st.add("B", BASE)
    plans = {a.id: (["x@x.com", "y@x.com"], 1, ""), b.id: (["Y@x.com", "z@x.com"], 0, "")}
    s = cs.queue_summary([a, b], plans, delay=40, daily_cap=10, sent_today=7)
    assert s["total"] == 3 and s["capped"] == 3 and list(s["overlaps"]) == ["y@x.com"]
    assert [(r["to_send"], r["overlap"], r["duplicates"]) for r in s["rows"]] == [(2, 0, 1), (1, 1, 0)]
    assert s["seconds"] == 3 * (cs.SEND_SECONDS + 40)
    assert cs.queue_summary([a, b], plans, 0, daily_cap=10, sent_today=9)["capped"] == 1
    assert cs.duration_text(3 * 60) == "about 3 min" and cs.duration_text(3725) == "about 1 h 02 min"


# ------------------------------------------------------------------ app-wide pace
class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_pace_is_shared_by_every_process(tmp_path):
    clock = Clock()
    path = tmp_path / "pace.json"
    a = pacing.Pace(path, pid=101, clock=clock, alive=lambda pid: True)
    b = pacing.Pace(path, pid=202, clock=clock, alive=lambda pid: True)
    assert a.try_acquire() == 0
    assert b.try_acquire() > 0                    # A is sending: B waits
    a.release(delay=30)
    assert 29 < b.try_acquire() <= 30             # the delay counts across campaigns
    clock.t += 30
    assert b.try_acquire() == 0
    assert a.try_acquire() > 0


def test_a_dead_holder_never_blocks_the_others(tmp_path):
    clock = Clock()
    path = tmp_path / "pace.json"
    dead = pacing.Pace(path, pid=101, clock=clock, alive=lambda pid: pid != 101)
    dead.try_acquire()
    b = pacing.Pace(path, pid=202, clock=clock, alive=lambda pid: pid != 101)
    assert b.try_acquire() == 0


def test_acquire_stops_cleanly(tmp_path):
    clock = Clock()
    a = pacing.Pace(tmp_path / "p.json", pid=1, clock=clock, alive=lambda p: True)
    b = pacing.Pace(tmp_path / "p.json", pid=2, clock=clock, sleep=lambda s: None, alive=lambda p: True)
    a.try_acquire()
    waits = []
    assert b.acquire(stop=lambda: len(waits) > 0, on_wait=waits.append) is False


# ------------------------------------------------------------------ the agent with the app-wide pace
@pytest.fixture
def agent(tmp_path, monkeypatch):
    from test_v36 import agent as _agent  # noqa: F401
    pytest.importorskip("playwright")
    import importlib
    from test_safety import _FakePlaywright

    def load(csv_path, **env):
        base = {"CRISP_CSV_FILE": str(csv_path), "CRISP_REFERENCE_FILTER": "R", "CRISP_GUI_MODE": "1",
                "CRISP_DRY_RUN": "false", "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "",
                "CRISP_SKIP_FAILED": "false", "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"),
                "CRISP_RUN_DIR": "", "CRISP_DELAY": "0", "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "",
                "CRISP_DAILY_CAP": "", "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false",
                "CRISP_HEALTH_CHECK": "false", "CRISP_SOURCE": "csv", "CRISP_CAMPAIGN": "B",
                "CRISP_RECENT_DAYS": "1", "CRISP_PACE_FILE": str(tmp_path / "pace.json")}
        base.update(env)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("crisp_email_agent", None)
        mod = importlib.import_module("crisp_email_agent")
        monkeypatch.setattr(mod, "sync_playwright", _FakePlaywright)
        monkeypatch.setattr(mod, "wait_for_crisp", lambda page: None)
        monkeypatch.setattr(mod, "recover_page", lambda page: True)
        return mod
    yield load
    sys.modules.pop("crisp_email_agent", None)


def write_list(path, emails):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Full Name", "Contact Email", "Reference", "Mail Sent"])
        for e in emails:
            w.writerow(["N", e, "R", "FALSE"])
    return path


def test_daily_cap_counts_every_campaign(agent, tmp_path, capsys):
    sc.append_history(tmp_path / "history.csv", Email="old@x.com", Result="Sent", Mode="live", Campaign="A")
    mod = agent(write_list(tmp_path / "b.csv", ["p1@x.com", "p2@x.com", "p3@x.com"]), CRISP_DAILY_CAP="3")
    sent = []

    def process(page, row):
        sent.append(row["Contact Email"])
        if len(sent) == 1:          # meanwhile another campaign (another process) sends one too
            sc.append_history(tmp_path / "history.csv", Email="other@x.com", Result="Sent", Mode="live",
                              Campaign="C")
        return True
    mod.process_recipient = process
    mod.main()
    out = capsys.readouterr().out
    assert sent == ["p1@x.com"]                    # 1 old + 1 here + 1 by the other campaign = cap 3
    assert "Daily cap reached: 3 of 3 sent today by all campaigns" in out
    state = json.loads((tmp_path / "pace.json").read_text())
    assert state["pid"] == 0                       # the send turn was handed back


def test_two_campaigns_with_the_same_person_email_them_once(agent, tmp_path, capsys):
    mod = agent(write_list(tmp_path / "a.csv", ["same@x.com", "a@x.com"]), CRISP_CAMPAIGN="A")
    sent = []
    mod.process_recipient = lambda page, row: sent.append(("A", row["Contact Email"])) or True
    mod.main()
    mod = agent(write_list(tmp_path / "b.csv", ["same@x.com", "b@x.com"]), CRISP_CAMPAIGN="B")
    mod.process_recipient = lambda page, row: sent.append(("B", row["Contact Email"])) or True
    mod.main()
    assert sent == [("A", "same@x.com"), ("A", "a@x.com"), ("B", "b@x.com")]


# ------------------------------------------------------------------ queue controller (fake runners)
class FakeRunner(QObject):
    finished = Signal(object)

    def __init__(self):
        super().__init__()
        self.running, self.paused, self.stopped = False, False, False
        self.state = None
        self.run_dir = ""

    def is_running(self):
        return self.running

    def pause(self):
        self.paused = True
        if self.state:
            self.state.paused = True
        return True

    def resume(self):
        self.paused = False

    def request_stop(self):
        self.stopped = True
        return "graceful"

    def end(self, phase=rs.FINISHED, unconfirmed=False, completed=2):
        self.running = False
        s = rs.RunState(dry_run=False, phase=phase, completed=completed, unconfirmed_send=unconfirmed)
        self.state = s
        return s


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def controller(st, parallel=1):
    main = FakeRunner()
    extra = {}
    launched = []

    def launch(c, runner, slot, dry):
        runner.running = True
        runner.state = rs.RunState(dry_run=dry, phase=rs.RUNNING, eligible=5, position=1)
        launched.append((c.name, slot))
        return None
    q = QueueController(st, launch, main, ROOT / "crisp_email_agent.py", lambda: parallel)
    q._extra_runner = lambda k: extra.setdefault(k, FakeRunner())
    return q, main, launched, extra


def finish(q, runner, **kw):
    q.runner_finished(runner, runner.end(**kw))


def test_queue_runs_in_order_one_at_a_time(qapp, tmp_path):
    st = store(tmp_path)
    a, b = st.add("A", BASE), st.add("B", BASE)
    st.enqueue(a.id)
    st.enqueue(b.id)
    q, main, launched, _ = controller(st)
    q.start(dry=False)
    assert launched == [("A", 0)] and st.get(b.id).status == cs.QUEUED
    finish(q, main)
    q.fill()                                       # (normally via a 0 ms timer)
    assert launched == [("A", 0), ("B", 0)] and st.get(a.id).status == cs.FINISHED
    finish(q, main)
    q.fill()
    assert st.get(b.id).status == cs.FINISHED and not q.active


def test_stopping_one_campaign_never_touches_the_queued_one(qapp, tmp_path):
    st = store(tmp_path)
    a, b = st.add("A", BASE), st.add("B", BASE)
    st.enqueue(a.id)
    st.enqueue(b.id)
    q, main, launched, _ = controller(st)
    q.start(dry=False)
    assert q.stop(a.id) == "graceful" and main.stopped
    assert st.get(b.id).status == cs.QUEUED
    finish(q, main, phase=rs.STOPPED)
    q.fill()
    assert st.get(a.id).status == cs.STOPPED and launched[-1] == ("B", 0)


def test_a_failure_or_unconfirmed_send_halts_the_queue(qapp, tmp_path):
    st = store(tmp_path)
    a, b = st.add("A", BASE), st.add("B", BASE)
    st.enqueue(a.id)
    st.enqueue(b.id)
    q, main, launched, _ = controller(st)
    halted = []
    q.halted.connect(halted.append)
    q.start(dry=False)
    finish(q, main, phase=rs.FAILED, unconfirmed=True)
    q.fill()
    assert st.get(a.id).status == cs.UNCONFIRMED and launched == [("A", 0)] and halted and not q.active
    assert st.get(b.id).status == cs.QUEUED                          # kept for later


def test_pause_resume_and_close_while_paused(qapp, tmp_path):
    st = store(tmp_path)
    a, b = st.add("A", BASE), st.add("B", BASE)
    st.enqueue(a.id)
    st.enqueue(b.id)
    q, main, launched, _ = controller(st)
    q.start(dry=False)
    assert q.pause(a.id) and main.paused and st.get(a.id).status == cs.PAUSED
    q.fill()
    assert launched == [("A", 0)]                                    # one browser: B waits
    q.close_all()                                                    # app closed while paused
    again = store(tmp_path)
    again.restore()
    assert again.get(a.id).status == cs.PAUSED and again.queue[0] == a.id
    assert again.get(b.id).status == cs.QUEUED


def test_close_during_a_send_marks_unconfirmed(qapp, tmp_path):
    st = store(tmp_path)
    a = st.add("A", BASE)
    st.enqueue(a.id)
    q, main, _l, _e = controller(st)
    q.start(dry=False)
    main.state.position, main.state.completed = 3, 2                 # recipient 3 in flight
    q.close_all()
    assert st.get(a.id).status == cs.UNCONFIRMED


def test_parallel_mode_uses_separate_browsers(qapp, tmp_path):
    st = store(tmp_path)
    a, b, c = (st.add(n, BASE) for n in ("A", "B", "C"))
    for x in (a, b, c):
        st.enqueue(x.id)
    q, main, launched, extra = controller(st, parallel=2)
    q.start(dry=True)
    assert launched == [("A", 0), ("B", 1)]
    finish(q, extra[1])
    q.fill()
    assert launched[-1] == ("C", 1) and st.get(a.id).status == cs.RUNNING


# ------------------------------------------------------------------ GUI: save, queue, confirm, run
def test_gui_queue_of_two_campaigns_runs_both_with_one_confirmation(qapp, tmp_path, monkeypatch):
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from crisp_gui import widgets as W
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager

    seen = []
    monkeypatch.setattr(W, "DIALOG_HOOK", lambda title, body, rows, confirm: seen.append((title, body, confirm))
                        or True)
    for k in ("FAKE_FAIL_EMAIL", "FAKE_LOGIN_WAIT", "FAKE_STEP_DELAY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("FAKE_ENV_OUT", str(tmp_path / "env.json"))
    monkeypatch.setenv("CRISP_API_CONFIG", str(tmp_path / "crisp_api.json"))   # never the real token
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    w = MainWindow(ThemeManager(qapp, "light"), prefs, agent_script=tg.FAKE, profile_dir=tmp_path / "profile",
                   data_dir=tmp_path / "data")
    try:
        c = w.campaign
        for name, n in (("First", 2), ("Second", 3)):
            path = tg.make_csv(tmp_path / f"{name}.csv", n=n)
            c.csv.edit.setText(str(path))
            c.campaign_name.edit.setText(name)
            w.save_campaign_new()
            w._campaign_action(w.campaign_store.enqueue)
        assert [x.status for x in w.campaign_store.list()] == [cs.QUEUED, cs.QUEUED]
        w.campaign.dry.setChecked(False)                             # real sending: one confirmation
        w.run_queue()
        title, body, confirm = seen[-1]
        # both lists hold u0 and u1: they are counted (and sent) once, by the first campaign
        assert title == "Send 3 real emails in 2 campaigns?" and "First, Second" in confirm
        assert "REAL SENDING" in body and "2 person(s) are in more than one campaign" in body
        assert tg.wait_until(lambda: not w.queue_ctl.active and not w.runner.is_running(), 30)
        assert [x.status for x in w.campaign_store.list()] == [cs.FINISHED, cs.FINISHED]
        env = json.loads((tmp_path / "env.json").read_text())
        assert env["CRISP_CAMPAIGN"] == "Second" and env["CRISP_PACE_FILE"].endswith("pace.json")
        assert env["CRISP_RECENT_DAYS"] == "7" and env["CRISP_DRY_RUN"] == "false"
    finally:
        if w.runner.is_running():
            w.runner.force_stop()
            w.runner.proc.waitForFinished(3000)
        w.close()

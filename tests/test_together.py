"""Browser, Crisp API and My email campaigns running side by side; per-channel daily counts."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import send_control as sc  # noqa: E402
from crisp_gui import campaign_store as cs  # noqa: E402
from crisp_gui.queue_controller import QueueController  # noqa: E402
from test_campaigns import BASE, ROOT, FakeRunner, finish, qapp, rs, store  # noqa: E402,F401
from test_v36 import agent  # noqa: E402,F401

API = dict(BASE, send_mode="api", api_message="Hi {first_name}")
EMAIL = dict(BASE, send_mode="email", api_message="Hi {first_name}", segment="")


def controller(st, parallel=1, together=True):
    main, extra, launched = FakeRunner(), {}, []

    def launch(c, runner, slot, dry):
        runner.running = True
        runner.state = rs.RunState(dry_run=dry, phase=rs.RUNNING, eligible=5, position=1)
        launched.append(c.name)
        return None
    q = QueueController(st, launch, main, ROOT / "crisp_email_agent.py", lambda: parallel, together=lambda: together)
    q._extra_runner = lambda k: extra.setdefault(k, FakeRunner())
    return q, main, launched, extra


def queued(st, *items):
    out = []
    for name, settings in items:
        c = st.add(name, settings)
        st.enqueue(c.id)
        out.append(c)
    return out


def test_one_of_each_type_runs_at_the_same_time(qapp, tmp_path):
    st = store(tmp_path)
    queued(st, ("Shortcut", BASE), ("Shortcut 2", BASE), ("API", API), ("Mail", EMAIL))
    q, main, launched, extra = controller(st)
    q.start(dry=False)
    assert launched == ["Shortcut", "API", "Mail"]            # the second Browser one waits its turn
    browser_runner = main
    finish(q, browser_runner)
    q.fill()
    assert launched[-1] == "Shortcut 2"


def test_together_off_keeps_one_at_a_time(qapp, tmp_path):
    st = store(tmp_path)
    queued(st, ("Shortcut", BASE), ("API", API), ("Mail", EMAIL))
    q, main, launched, _ = controller(st, together=False)
    q.start(dry=False)
    assert launched == ["Shortcut"]


def test_daily_counts_are_per_channel(tmp_path):
    hist = tmp_path / "history.csv"
    for channel in ("", "crisp", "email", "email"):           # "" = a row from before the Channel column
        sc.append_history(hist, Email="a@x.com", Result=sc.SENT, Mode="live", Channel=channel)
    now = datetime.now(sc.DHAKA)
    assert sc.sent_today(hist, now) == 4
    assert sc.sent_today(hist, now, channel="crisp") == 2
    assert sc.sent_today(hist, now, channel="email") == 2


def test_my_email_ignores_crisp_sends_and_uses_its_own_pace(agent, tmp_path, monkeypatch):
    import test_mail_sender as tm
    tm.FakeSMTP.instances, tm.FakeSMTP.fail, tm.FakeSMTP.noop_code = [], None, 250
    hist = tmp_path / "history.csv"
    for _ in range(5):                                         # Crisp already sent its 5 for today
        sc.append_history(hist, Email="c@x.com", Result=sc.SENT, Mode="live", Channel="crisp")
    path = tm.write(tmp_path / "r.csv", tm.HEADER, [["Ann", "ann@x.com", "Shahriar", "FALSE", ""]])
    mod = tm.mail_agent(agent, tmp_path, monkeypatch, path, ("Hi",), CRISP_DAILY_CAP="5",
                        CRISP_PACE_FILE=str(tmp_path / "pace.json"))
    assert mod.PACE_FILE.endswith("pace_email.json") and mod.DAILY_CAP == ""
    mod.main()
    assert len([m for i in tm.FakeSMTP.instances for m in i.sent]) == 1
    assert sc.read_history(hist)[-1]["Channel"] == "email"


def test_a_run_started_by_hand_counts_for_its_type(qapp, tmp_path):
    st = store(tmp_path)
    shortcut, api = queued(st, ("Shortcut", BASE), ("API", API))
    q, main, launched, _ = controller(st)
    main.running, main.channel = True, "browser"               # a manual Browser run is going
    assert not q.can_start(shortcut) and q.can_start(api)
    q.start(dry=False)
    assert launched == ["API"]

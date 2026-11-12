"""Scheduled campaigns (start by themselves at a Dhaka time; never sent late) and the
'Read at' window from automatic Crisp API checks."""

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import campaign_results as cr  # noqa: E402
import crisp_api as ca  # noqa: E402
import send_control as sc  # noqa: E402
from crisp_gui import campaign_store as cs  # noqa: E402
from crisp_gui import schedule_dialog  # noqa: E402
from test_crisp_api import WID, FakeCrisp, link, ours, reply, sid  # noqa: E402
from test_gui import app, dialogs, env, make_csv, wait_until, win  # noqa: E402,F401

BASE = {"source": "csv", "csv": "x.csv", "subject": "S", "shortcut": "!a", "segment": "g"}


# ------------------------------------------------------------------ store rules
def test_schedule_due_and_missed(tmp_path):
    st = cs.CampaignStore(tmp_path / "c.json")
    a, b, d = st.add("A", BASE), st.add("B", BASE), st.add("Draft", dict(BASE, subject=""))
    with pytest.raises(ValueError, match="missing"):
        st.schedule(d.id, "2026-11-13 10:00", live=True)
    st.schedule(a.id, "2026-11-13 10:00", live=True)
    st.schedule(b.id, "2026-11-13 09:00", live=False)
    assert [c.name for c in st.scheduled()] == ["B", "A"] and st.get(a.id).status == cs.SCHEDULED
    start, missed = st.due("2026-11-13 09:59")
    assert [c.name for c in start] == [] and [c.name for c in missed] == ["B"]     # 59 min late: missed
    assert st.get(b.id).status == cs.READY and "Nothing was sent" in st.get(b.id).note
    start, missed = st.due("2026-11-13 10:05")
    assert [c.name for c in start] == ["A"] and missed == []
    again = cs.CampaignStore(tmp_path / "c.json")
    assert any("scheduled for 2026-11-13 10:00" in n and "REAL SENDING" in n for n in again.restore())
    again.unschedule(a.id, "Schedule cancelled.")
    assert again.get(a.id).status == cs.READY and again.get(a.id).schedule == {}


# ------------------------------------------------------------------ read time window
def test_read_at_is_the_window_between_two_checks(tmp_path):
    st = cr.ResultsStore(tmp_path / "r.json")
    st.sync_history([{"Time": "2026-11-12 09:00:00", "Email": "a@x.com", "Result": "Sent", "Mode": "live",
                      "Campaign": "Wow", "Link": link("aaaa1111")}])
    key = cr.rkey("Wow", "a@x.com")
    st.apply(key, {"status": cr.DELIVERED, "problem": ""}, datetime(2026, 11, 12, 10, 0))
    st.apply(key, {"status": cr.READ, "problem": ""}, datetime(2026, 11, 12, 10, 30))
    row = cr.table_row(st.recipients[key])
    assert cr.TABLE_COLUMNS[5] == "Read at" and row[5] == "2026-11-12 10:00–10:30"
    assert row[4] == "2026-11-12 10:00 (first seen)"                         # no earlier check
    assert cr.window("2026-11-12 23:40", "2026-11-13 00:10") == "between 2026-11-12 23:40 and 2026-11-13 00:10"


# ------------------------------------------------------------------ in the app
def test_automatic_check_is_quiet_and_skips_replied(win, dialogs, monkeypatch, tmp_path):
    now = datetime.now()
    sent = now.strftime("%Y-%m-%d %H:%M:%S")
    hist = tmp_path / "data" / "history.csv"
    for name, email in (("aaaa1111", "a@x.com"), ("bbbb2222", "b@x.com")):
        sc.append_history(hist, Time=sent, Email=email, Result="Sent", Mode="live", Campaign="Wow", Link=link(name))
    win.results.sync_history(sc.read_history(hist))
    win.results.apply(cr.rkey("Wow", "b@x.com"), {"status": cr.REPLIED, "reply_at": sent[:16], "problem": ""})
    ca.save_config({"website_id": WID, "identifier": "id", "key": "k"}, win.api_config_path)
    fake = FakeCrisp({sid("aaaa1111"): {"state": "resolved",
                                        "messages": [ours(timestamp=int(now.timestamp() * 1000) + 1000, read="email")]}})
    monkeypatch.setattr(win, "_api_factory", lambda cfg: ca.CrispAPI(cfg["website_id"], cfg["identifier"],
                                                                     cfg["key"], opener=fake, sleep=lambda s: None))
    seen_before = list(dialogs["seen"])
    win.auto_check_statuses()
    assert wait_until(lambda: not win._api_checking, 10)
    assert dialogs["seen"] == seen_before                                    # no dialog at all
    rows = {r["email"]: r for r in win.results.rows("Wow")}
    assert rows["a@x.com"]["status"] == cr.READ and rows["a@x.com"]["read_at"]
    urls = [u for _m, u, _h in fake.seen]
    assert all("bbbb2222" not in u for u in urls)                            # replied: not checked again
    win.prefs.set_auto_check(False)
    win.auto_check_statuses()
    assert not win._api_checking


def test_schedule_live_then_start_at_the_time(win, dialogs, monkeypatch, tmp_path):
    store = win.campaign_store
    csv1 = make_csv(tmp_path / "one.csv", n=2)
    a = store.add("Planned", {"source": "csv", "csv": str(csv1), "subject": "S", "shortcut": "!a",
                              "segment": "g", "reference": "Shahriar"})
    other = store.add("Other", {"source": "csv", "csv": str(csv1), "subject": "S", "shortcut": "!a", "segment": "g"})
    store.enqueue(other.id)
    win.navigate(0)
    win.refresh_campaigns(select=a.id)
    monkeypatch.setattr(schedule_dialog, "SCHEDULE_HOOK", lambda default: "2099-01-01 10:00")
    win.campaign.dry.setChecked(False)                                     # the top switch says LIVE
    win.schedule_campaign()
    assert dialogs["seen"][-1] == "Schedule REAL sending of 'Planned' at 2099-01-01 10:00?"
    c = store.get(a.id)
    assert c.status == cs.SCHEDULED and c.schedule == {"at": "2099-01-01 10:00", "live": True}
    assert "Scheduled: 'Planned' at 2099-01-01 10:00" in win.campaigns_page.banner.text.text()
    win.check_schedules("2099-01-01 09:59")
    assert store.get(a.id).status == cs.SCHEDULED                          # not yet
    win.check_schedules("2099-01-01 10:01")
    assert wait_until(lambda: not win.queue_ctl.active and not win.runner.is_running(), 30)
    assert store.get(a.id).status == cs.FINISHED
    assert store.get(other.id).status == cs.QUEUED                         # only the scheduled one ran
    assert not store.get(a.id).last.get("dry_run")                        # it ran live, as confirmed


def test_a_missed_schedule_is_never_sent_late(win, dialogs):
    store = win.campaign_store
    a = store.add("Late", BASE)
    store.schedule(a.id, "2099-01-01 10:00", live=True)
    win.check_schedules("2099-01-01 11:00")
    assert store.get(a.id).status == cs.READY and not win.runner.is_running()
    assert "Scheduled campaign missed" in dialogs["seen"]

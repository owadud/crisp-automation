"""Phase 4: limits, delays, send window, scheduled start, records. Browser stubbed."""

import csv
import importlib
import os
import sys
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import send_control as sc  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.csv_stats import read_stats  # noqa: E402
from crisp_gui.diagnostics import build_zip, mask_emails  # noqa: E402

from test_safety import _FakePlaywright, write_csv  # noqa: E402

D = sc.DHAKA


def at(h, m=0, day=4):
    return datetime(2026, 11, day, h, m, tzinfo=D)


# ------------------------------------------------------------------ pure helpers
def test_window_parsing_and_membership():
    w = sc.parse_window("10:00-18:00")
    assert w == ((10, 0), (18, 0))
    assert sc.in_window(at(10), w) and sc.in_window(at(17, 59), w)
    assert not sc.in_window(at(18), w) and not sc.in_window(at(9, 59), w)
    night = sc.parse_window("22:00 - 02:00")
    assert sc.in_window(at(23), night) and sc.in_window(at(1), night) and not sc.in_window(at(12), night)
    assert sc.parse_window("") is None
    for bad in ("10-18", "25:00-26:00", "10:00", "10:00-10:00"):
        with pytest.raises(ValueError):
            sc.parse_window(bad)
    assert sc.window_opens_later_today(at(8), w) == at(10)
    assert sc.window_opens_later_today(at(19), w) is None


def test_next_occurrence_is_today_or_tomorrow():
    assert sc.next_occurrence("10:00", at(9)) == at(10)
    assert sc.next_occurrence("10:00", at(11)) == at(10, day=5)


def test_history_counts_only_live_sends(tmp_path):
    h = tmp_path / "history.csv"
    for result, mode in (("Sent", "live"), ("Sent", "live"), ("Dry run", "dry"), ("Failed", "live")):
        sc.append_history(h, Email="a@x.com", Result=result, Mode=mode)
    assert sc.sent_today(h) == 2 and sc.sent_last_days(h, 7) == 2
    assert sc.sent_today(h, sc.now_dhaka() + timedelta(days=2)) == 0


def test_old_runs_are_deleted(tmp_path):
    old = tmp_path / "2026-09-01_100000_live"
    new = tmp_path / "2026-11-04_100000_dry"
    other = tmp_path / "keep-me"
    for p in (old, new, other):
        p.mkdir()
    past = time.time() - 40 * 86400
    os.utime(old, (past, past))
    os.utime(other, (past, past))
    assert sc.delete_old_runs(tmp_path, 30) == [old]
    assert new.exists() and other.exists()


def test_diagnostics_mask_emails(tmp_path):
    assert mask_emails("to jane.doe@example.com ok") == "to j***@example.com ok"
    z = build_zip(tmp_path, ROOT, "sent to jane.doe@example.com", {"csv": str(tmp_path / "secret.csv"),
                                                                    "subject": "Hi"}, tmp_path / "p", tmp_path / "h")
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
        assert names == {"log_masked.txt", "versions.json", "settings_masked.json", "self_test.txt"}
        assert "jane.doe@" not in zf.read("log_masked.txt").decode()
        assert str(tmp_path) not in zf.read("settings_masked.json").decode()


# ------------------------------------------------------------------ agent
@pytest.fixture
def load_agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")

    def load(rows, **env):
        csv_path = write_csv(tmp_path / "agent.csv", rows)
        base = {"CRISP_CSV_FILE": str(csv_path), "CRISP_REFERENCE_FILTER": "R", "CRISP_GUI_MODE": "1",
                "CRISP_DRY_RUN": "true", "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "",
                "CRISP_SKIP_FAILED": "false", "CRISP_MAX_SKIPS": "5", "CRISP_MAX_FAILS_IN_ROW": "3",
                "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"), "CRISP_RUN_DIR": str(tmp_path / "run"),
                "CRISP_DELAY": "0", "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "",
                "CRISP_DAILY_CAP": "", "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false"}
        base.update(env)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("crisp_email_agent", None)
        mod = importlib.import_module("crisp_email_agent")
        return mod, csv_path
    yield load
    sys.modules.pop("crisp_email_agent", None)


def run_main(mod, monkeypatch, capsys, fail=None):
    fail = fail or {}
    monkeypatch.setattr(mod, "sync_playwright", _FakePlaywright)
    monkeypatch.setattr(mod, "wait_for_crisp", lambda page: None)
    monkeypatch.setattr(mod, "recover_page", lambda page: True)

    def process(page, row):
        if fail.get(row["Contact Email"]):
            raise RuntimeError("Subject not found")
        print("  Email sent successfully." if not mod.DRY_RUN else "  [DRY RUN] Send skipped.")
        return True
    monkeypatch.setattr(mod, "process_recipient", process)
    mod.main()
    out = capsys.readouterr().out
    parser = rs.RunParser(dry_run=mod.DRY_RUN)
    parser.feed(out.encode("utf-8"))
    return out, parser.finish(0)


def read_rows(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


ROWS = [(n, f"{n}@x.com", "R", "") for n in ("a", "b", "c", "d")]


def test_live_run_writes_columns_history_and_run_folder(load_agent, monkeypatch, capsys, tmp_path):
    rows = ROWS[:2] + [("bad", "bad@gmial.com", "R", "")]
    mod, csv_path = load_agent(rows, CRISP_DRY_RUN="false", CRISP_SKIP_FAILED="true")
    out, st = run_main(mod, monkeypatch, capsys, fail={"b@x.com": True})
    data = read_rows(csv_path)
    assert [r["Status"] for r in data] == ["Sent", "Skipped", "Invalid"]
    assert data[0]["Mail Sent"] == "TRUE" and data[0]["Sent At"] and data[1]["Mail Sent"] == ""
    assert "Subject not found" in data[1]["Error"]
    hist = read_rows(tmp_path / "history.csv")
    assert [(h["Email"], h["Result"], h["Mode"]) for h in hist] == [("a@x.com", "Sent", "live"),
                                                                     ("b@x.com", "Skipped", "live")]
    run = tmp_path / "run"
    assert {"recipients_before.csv", "recipients_after.csv", "report.csv"} <= {p.name for p in run.iterdir()}
    assert [r["Result"] for r in read_rows(run / "report.csv")] == ["Sent", "Skipped"]
    assert list(tmp_path.glob("agent.*.bak.csv")), "CSV backup missing"


def test_retry_failed_only_matches_preview(load_agent, monkeypatch, capsys, tmp_path):
    rows = [("a", "a@x.com", "R", ""), ("b", "b@x.com", "R", ""), ("c", "c@x.com", "R", "")]
    path = write_csv(tmp_path / "agent.csv", rows)
    data = read_rows(path)
    data[0]["Status"], data[1]["Status"] = "Failed", "Unconfirmed"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    assert read_stats(str(path), "R").retry_candidates == 1
    mod, _ = load_agent([], CRISP_RETRY_ONLY="true")
    path.write_bytes((tmp_path / "agent.csv").read_bytes())
    # reload with the status file in place
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        w.writeheader()
        w.writerows(data)
    agent_rows, _ = mod.load_csv()
    assert [r["Contact Email"] for _, r in mod.get_eligible(agent_rows)] == ["a@x.com"]


def test_max_per_run_and_daily_cap(load_agent, monkeypatch, capsys, tmp_path):
    for _ in range(2):
        sc.append_history(tmp_path / "history.csv", Email="z@x.com", Result="Sent", Mode="live")
    mod, _ = load_agent(ROWS, CRISP_DRY_RUN="false", CRISP_DAILY_CAP="3")
    out, st = run_main(mod, monkeypatch, capsys)
    assert st.completed == 1 and "Limit: sending 1 of 4" in out

    mod, _ = load_agent(ROWS, CRISP_MAX_PER_RUN="2")
    out, st = run_main(mod, monkeypatch, capsys)
    assert st.completed == 2 and st.eligible == 2


def test_daily_cap_reached_sends_nothing(load_agent, monkeypatch, capsys, tmp_path):
    sc.append_history(tmp_path / "history.csv", Email="z@x.com", Result="Sent", Mode="live")
    mod, _ = load_agent(ROWS, CRISP_DRY_RUN="false", CRISP_DAILY_CAP="1")
    out, st = run_main(mod, monkeypatch, capsys)
    assert "Daily cap reached" in out and st.completed == 0 and st.phase == rs.FINISHED


def test_send_window_ended_stops_before_sending(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS, CRISP_SEND_WINDOW="10:00-18:00")
    monkeypatch.setattr(mod.sc, "now_dhaka", lambda: at(19))
    out, st = run_main(mod, monkeypatch, capsys)
    assert "has ended" in out and st.completed == 0 and st.phase == rs.STOPPED


def test_scheduled_start_waits_then_runs(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS[:1], CRISP_START_AT="10:00")
    waited = []
    monkeypatch.setattr(mod.sc, "next_occurrence", lambda hhmm: at(10))
    monkeypatch.setattr(mod.sc, "sleep_until", lambda target, stop: waited.append(target) or True)
    out, st = run_main(mod, monkeypatch, capsys)
    assert waited == [at(10)] and "Scheduled start: waiting until 2026-11-04 10:00" in out
    assert st.completed == 1


def test_bad_window_is_an_error_not_a_send(load_agent, monkeypatch, capsys):
    mod, _ = load_agent(ROWS, CRISP_SEND_WINDOW="10-18")
    with pytest.raises(ValueError):
        run_main(mod, monkeypatch, capsys)

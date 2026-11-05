"""Email-only input, history rules (recent / per campaign), pause/resume, ETA,
dry run writes nothing. Browser and Google are stubbed."""

import csv
import importlib
import json
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import recipient_rules as rules  # noqa: E402
import send_control as sc  # noqa: E402
import sheet_source as ss  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.csv_stats import read_stats  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402
from test_safety import _FakePlaywright  # noqa: E402


def write(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def read(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        r = csv.reader(f)
        return list(r)


def hist(path, email, result="Sent", mode="live", campaign="Old", days_ago=1):
    t = sc.stamp(sc.now_dhaka() - timedelta(days=days_ago))
    new = not path.exists()
    with open(path, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sc.HISTORY_COLUMNS)
        if new:
            w.writeheader()
        w.writerow({"Time": t, "Email": email, "Result": result, "Mode": mode, "Campaign": campaign})


# ------------------------------------------------------------------ columns
@pytest.mark.parametrize("header", ["Contact Email", "Email", "email", " E-mail ", "E_MAIL", "e mail"])
def test_email_header_variants(header):
    assert rules.map_columns(["Notes", header])["Contact Email"] == header


def test_email_only_file_summary(tmp_path):
    p = write(tmp_path / "r.csv", ["E-mail", "Notes"],
              [["a@x.com", "n1"], ["b@x.com", "n2"], ["bad@", "n3"], ["A@x.com", "n4"]])
    s = read_stats(str(p), "Shahriar")
    assert s.ok and s.email_column == "E-mail"
    assert s.missing_optional == ["Full Name", "Reference", "Mail Sent"]
    assert s.preview() == ("Found 4 rows · 3 valid emails · 1 invalid · 1 duplicate skipped · 2 to send · "
                           "optional columns missing: Full Name, Reference, Mail Sent")
    names = {p["email"]: (p["name"], p["fallback"]) for p in s.plan}
    assert names["a@x.com"] == ("A", True)


def test_name_fallback_never_blank():
    assert rules.display_name({"Full Name": "", "Contact Email": "john.doe+vip@x.com"}) == ("John Doe", True)
    assert rules.display_name({"Full Name": "nan", "Contact Email": "9_9@x.com"}) == ("Customer", True)
    assert rules.display_name({"Full Name": "  Ana   Lee ", "Contact Email": "a@x.com"}) == ("Ana Lee", False)


def test_empty_reference_is_sent_other_reference_is_not():
    rows = [{"Contact Email": "a@x.com", "Reference": ""}, {"Contact Email": "b@x.com", "Reference": "Other"},
            {"Contact Email": "c@x.com", "Reference": "shahriar"}]
    out = [o for _i, _r, o, _d in rules.classify(rows, "Shahriar", 1, 3)]
    assert out == [rules.ELIGIBLE, rules.OTHER_REFERENCE, rules.ELIGIBLE]


# ------------------------------------------------------------------ history rules
def test_recent_and_campaign_rules(tmp_path):
    h = tmp_path / "history.csv"
    hist(h, "recent@x.com", campaign="Black Friday", days_ago=3)
    hist(h, "old@x.com", campaign="Black Friday", days_ago=30)
    hist(h, "same@x.com", campaign="VIP", days_ago=30)
    hist(h, "maybe@x.com", result="Unconfirmed", campaign="Other", days_ago=2)
    hist(h, "dry@x.com", mode="dry", campaign="VIP", days_ago=1)
    hist(h, "failed@x.com", result="Failed", campaign="VIP", days_ago=1)
    emails = ["recent@x.com", "old@x.com", "same@x.com", "maybe@x.com", "dry@x.com", "failed@x.com"]
    p = write(tmp_path / "r.csv", ["Email"], [[e] for e in emails])
    s = read_stats(str(p), "", history_file=str(h), campaign="VIP", recent_days=7)
    action = {x["email"]: x["action"] for x in s.plan}
    assert action == {"recent@x.com": "Skip: emailed recently", "old@x.com": "Send",
                      "same@x.com": "Skip: already sent in this campaign", "maybe@x.com": "Skip: emailed recently",
                      "dry@x.com": "Send", "failed@x.com": "Send"}
    assert (s.recent, s.campaign_sent) == (2, 1)
    assert "2 skipped: emailed in the last 7 days" in s.preview()
    assert any("unconfirmed" in d for _n, _e, d in s.recent_rows)
    off = read_stats(str(p), "", history_file=str(h), campaign="VIP", recent_days=0)
    assert off.recent == 0 and off.campaign_sent == 1


def test_old_history_file_is_upgraded(tmp_path):
    h = tmp_path / "history.csv"
    write(h, ["Time", "Email", "Name", "Result", "Subject", "Mode", "Error", "Row", "CSV"],
          [["2026-11-01 10:00:00", "a@x.com", "A", "Sent", "S", "live", "", "1", "r.csv"]])
    sc.append_history(h, Email=" B@X.com ", Result="Sent", Mode="live", Campaign="VIP")
    data = read(h)
    assert data[0] == sc.HISTORY_COLUMNS
    assert data[1][1] == "a@x.com" and data[2][1] == "b@x.com" and data[2][4] == "VIP"


# ------------------------------------------------------------------ agent
@pytest.fixture
def agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")

    def load(csv_path, **env):
        base = {"CRISP_CSV_FILE": str(csv_path), "CRISP_REFERENCE_FILTER": "Shahriar", "CRISP_GUI_MODE": "1",
                "CRISP_DRY_RUN": "false", "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "",
                "CRISP_SKIP_FAILED": "false", "CRISP_MAX_SKIPS": "5", "CRISP_MAX_FAILS_IN_ROW": "3",
                "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"), "CRISP_RUN_DIR": "", "CRISP_DELAY": "0",
                "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "", "CRISP_DAILY_CAP": "",
                "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false",
                "CRISP_HEALTH_CHECK": "false", "CRISP_SOURCE": "csv", "CRISP_CAMPAIGN": "VIP",
                "CRISP_RECENT_DAYS": "7", "CRISP_PAUSE_FILE": str(tmp_path / "pause.flag")}
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


def run(mod, capsys, on_process=None):
    names = []

    def process(page, row):
        names.append((row["Contact Email"], rules.display_name(row)[0]))
        if on_process:
            on_process(row)
        return True
    mod.process_recipient = process
    mod.main()
    out = capsys.readouterr().out
    p = rs.RunParser(dry_run=mod.DRY_RUN)
    p.feed(out.encode("utf-8"))
    return out, p.finish(0), names


def test_email_only_live_run_creates_columns_and_keeps_others(agent, capsys, tmp_path):
    p = write(tmp_path / "r.csv", ["email", "Notes"], [["ann.lee@x.com", "keep me"], ["bo@x.com", ""]])
    mod = agent(p)
    out, st, names = run(mod, capsys)
    assert st.completed == 2 and names == [("ann.lee@x.com", "Ann Lee"), ("bo@x.com", "Bo")]
    data = read(p)
    header = data[0]
    assert header[:2] == ["email", "Notes"]                         # user's own headers untouched
    assert {"Mail Sent", "Reference", "Status"} <= set(header)
    row = dict(zip(header, data[1]))
    assert (row["Notes"], row["Mail Sent"], row["Reference"], row["Status"]) == ("keep me", "TRUE", "Shahriar", "Sent")
    h = read(tmp_path / "history.csv")
    assert h[1][1] == "ann.lee@x.com" and h[1][2] == "Ann Lee" and h[1][4] == "VIP"


def test_new_mail_sent_column_starts_false(agent, capsys, tmp_path):
    p = write(tmp_path / "r.csv", ["Email"], [["a@x.com"], ["b@x.com"]])
    mod = agent(p, CRISP_MAX_PER_RUN="1")
    run(mod, capsys)
    data = read(p)
    col = data[0].index("Mail Sent")
    assert [r[col] for r in data[1:]] == ["TRUE", "FALSE"]


def test_recent_and_campaign_skips_in_the_agent(agent, capsys, tmp_path):
    h = tmp_path / "history.csv"
    hist(h, "recent@x.com", campaign="Other", days_ago=2)
    hist(h, "maybe@x.com", result="Unconfirmed", campaign="Other", days_ago=2)
    hist(h, "vip@x.com", campaign="VIP", days_ago=60)
    p = write(tmp_path / "r.csv", ["Email"], [["recent@x.com"], ["maybe@x.com"], ["vip@x.com"], ["new@x.com"]])
    mod = agent(p)
    out, st, names = run(mod, capsys)
    assert [e for e, _ in names] == ["new@x.com"]
    assert "EMAILED RECENTLY row 1: recent@x.com" in out
    assert "WARNING: maybe@x.com was possibly sent before (unconfirmed)" in out
    assert "ALREADY IN CAMPAIGN row 3: vip@x.com" in out


def test_dry_run_writes_nothing(agent, capsys, tmp_path):
    p = write(tmp_path / "r.csv", ["Email"], [["a@x.com"], ["b@x.com"]])
    before = p.read_bytes()
    mod = agent(p, CRISP_DRY_RUN="true")
    out, st, names = run(mod, capsys)
    assert st.completed == 2
    assert p.read_bytes() == before                                 # CSV / Mail Sent untouched
    assert not (tmp_path / "history.csv").exists()                  # no history
    assert not list(tmp_path.glob("*.bak.csv"))


def test_pause_resume_never_resends(agent, capsys, tmp_path):
    p = write(tmp_path / "r.csv", ["Email"], [["a@x.com"], ["b@x.com"], ["c@x.com"]])
    flag = tmp_path / "pause.flag"
    mod = agent(p, CRISP_DRY_RUN="true")

    def after_first(row):
        if row["Contact Email"] == "a@x.com":
            flag.write_text("pause")                                # user clicks Pause during a@
            threading.Timer(0.8, lambda: flag.unlink()).start()     # ...and Resume a bit later
    t = time.time()
    out, st, names = run(mod, capsys, on_process=after_first)
    assert [e for e, _ in names] == ["a@x.com", "b@x.com", "c@x.com"]   # each exactly once
    assert "Paused after a@x.com. Waiting for Resume..." in out and "Resumed." in out
    assert out.index("Paused after a@x.com") < out.index("[2/3] b@x.com")
    assert time.time() - t >= 0.7 and not st.paused and st.completed == 3


def test_stop_while_paused_stops_cleanly(agent, capsys, tmp_path, monkeypatch):
    p = write(tmp_path / "r.csv", ["Email"], [["a@x.com"], ["b@x.com"]])
    flag, stop = tmp_path / "pause.flag", tmp_path / "stop.flag"
    mod = agent(p, CRISP_DRY_RUN="true", CRISP_STOP_FILE=str(stop))

    def after_first(row):
        flag.write_text("pause")
        threading.Timer(0.5, lambda: stop.write_text("stop")).start()
    out, st, names = run(mod, capsys, on_process=after_first)
    assert [e for e, _ in names] == ["a@x.com"] and st.phase == rs.STOPPED


def test_sheet_with_only_email_column(agent, capsys, tmp_path, monkeypatch):
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    book = FakeSpreadsheet({"T": [["E-mail", "Notes"], ["a@x.com", "x"], ["b@x.com", "y"]]})
    real = ss.SheetSource.connect

    def connect(self):
        self._client = FakeClient(book, sid)
        return real(self)
    monkeypatch.setattr(ss.SheetSource, "connect", connect)
    monkeypatch.setattr(ss.SheetSource, "start_heartbeat", lambda self, interval=30: None)
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "service_account", "client_email": "bot@p.iam.gserviceaccount.com"}))
    mod = agent(tmp_path / "unused.csv", CRISP_SOURCE="sheet", CRISP_SHEET_TAB="T", CRISP_GOOGLE_KEY=str(key),
                CRISP_SHEET_URL=f"https://docs.google.com/spreadsheets/d/{sid}/edit",
                CRISP_DATA_DIR=str(tmp_path / "data"), CRISP_MAX_PER_RUN="1")
    out, st, names = run(mod, capsys)
    ws = book.tabs["T"]
    assert ws.values[0][:2] == ["E-mail", "Notes"]
    assert [ws.cell(r, "Mail Sent") for r in (2, 3)] == ["TRUE", "FALSE"]
    assert ws.cell(2, "Reference") == "Shahriar" and ws.cell(2, "Notes") == "x"


# ------------------------------------------------------------------ ETA
def test_eta_uses_real_pace_and_ignores_pause():
    now = [100.0]
    p = rs.RunParser(dry_run=True, clock=lambda: now[0])
    p.handle_line('@@EVENT {"event": "run_start", "dry_run": true, "delay": 40}')
    p.handle_line('@@EVENT {"event": "eligible", "count": 10}')
    st = p.state
    assert rs.eta_seconds(st, now[0]) == pytest.approx(10 * 65)           # 25 s work + 40 s delay
    p.handle_line('@@EVENT {"event": "recipient", "position": 1, "total": 10, "email": "a", "name": "A"}')
    now[0] = 130.0
    p.handle_line('@@EVENT {"event": "success", "email": "a", "result": "Dry run"}')
    p.handle_line('@@EVENT {"event": "paused", "after": "a"}')
    now[0] = 400.0                                                          # 270 s paused
    assert st.paused and st.paused_after == "a"
    p.handle_line('@@EVENT {"event": "resumed"}')
    p.handle_line('@@EVENT {"event": "recipient", "position": 2, "total": 10, "email": "b", "name": "B"}')
    now[0] = 430.0
    p.handle_line('@@EVENT {"event": "success", "email": "b", "result": "Dry run"}')
    # 2 done in 60 s of active time -> 30 s each -> 8 left = 240 s
    assert rs.eta_seconds(st, now[0]) == pytest.approx(240)

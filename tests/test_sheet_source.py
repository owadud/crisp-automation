"""Phase 6: Google Sheet as the source. gspread is replaced by an in-memory fake."""

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import sheet_source as ss  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from fake_sheets import FakeAPIError, FakeClient, FakeSpreadsheet  # noqa: E402
from test_safety import _FakePlaywright  # noqa: E402

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"
HEADER = ["Full Name", "Contact Email", "Reference", "Mail Sent"]


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)


def make_sheet(rows):
    return FakeSpreadsheet({"Customers": [HEADER] + [list(r) for r in rows], "Other": [["x"]]})


def source(book, tab="Customers"):
    return ss.SheetSource(URL, tab, "unused.json", client=FakeClient(book, SID)).connect()


def key_file(folder):
    path = Path(folder) / "key.json"
    path.write_text(json.dumps({"type": "service_account", "client_email": "bot@proj.iam.gserviceaccount.com"}))
    return path


ROWS = [("A", "a@x.com", "R", "FALSE"), ("B", "b@x.com", "R", "FALSE"), ("C", "c@x.com", "R", "FALSE")]


def test_link_and_key_checks(tmp_path):
    assert ss.sheet_id(URL) == SID
    with pytest.raises(ss.SheetError):
        ss.sheet_id("https://example.com/not-a-sheet")
    assert ss.key_problem(str(key_file(tmp_path)), ROOT) == ""
    inside = ROOT / "tests" / "_tmp_key.json"
    inside.write_text(json.dumps({"type": "service_account", "client_email": "x"}))
    try:
        assert "OUTSIDE the app folder" in ss.key_problem(str(inside), ROOT)
    finally:
        inside.unlink()
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"type": "authorized_user"}))
    assert "service-account" in ss.key_problem(str(bad), ROOT)


def test_download_tabs_and_push_by_email(tmp_path):
    book = make_sheet(ROWS)
    src = source(book)
    assert src.tabs() == ["Customers", "Other"]
    rows, header = src.download()
    assert [r["Contact Email"] for r in rows] == ["a@x.com", "b@x.com", "c@x.com"]
    src.ensure_columns(["Sent At", "Status"])
    ws = book.tabs["Customers"]
    ws.values.insert(1, ["New", "new@x.com", "R", "FALSE"])      # someone inserted a row meanwhile
    row = src.push("b@x.com", {"Mail Sent": "TRUE", "Status": "Sent"}, prefer_row=3)
    assert row == 4 and ws.cell(4, "Mail Sent") == "TRUE" and ws.cell(4, "Status") == "Sent"
    assert ws.cell(3, "Mail Sent") == "FALSE"                    # the row now at 3 (a@) untouched
    with pytest.raises(ss.SheetError, match="no longer in the Sheet"):
        src.push("gone@x.com", {"Status": "Sent"})


def test_missing_columns_and_bad_tab():
    book = FakeSpreadsheet({"T": [["Name", "Phone"], ["a", "123"]]})
    with pytest.raises(ss.SheetError, match="No email column"):
        source(book, "T").download()
    with pytest.raises(ss.SheetError, match="not found"):
        source(book, "Nope").download()


def test_pacer_spaces_calls_and_retries_rate_limits():
    t = [0.0]
    slept = []
    p = ss.Pacer(interval=1.0, clock=lambda: t[0], sleep=lambda s: (slept.append(s), t.__setitem__(0, t[0] + s)))
    p.call(lambda: 1)
    p.call(lambda: 2)
    assert slept == [1.0]
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise FakeAPIError(429, "quota")
        return "ok"
    assert p.call(flaky) == "ok" and len(calls) == 3
    with pytest.raises(ss.SheetError):
        p.call(lambda: (_ for _ in ()).throw(FakeAPIError(403, "forbidden")))


def old_version_lock(book, now, owner="OTHER-PC"):
    """What app 3.7 and older write: one lock for the whole Sheet."""
    src = source(book)
    src._control().update(values=[[f"Running: {owner} since 10:00 (Dhaka)", now.isoformat(), owner]],
                          range_name="A1")


def test_shared_marker_never_blocks_new_runners_but_keeps_old_versions_out():
    book = make_sheet(ROWS)
    now = datetime(2026, 11, 4, 4, 0, tzinfo=timezone.utc)
    a, b = source(book), source(book)
    a.owner, b.owner = "PC-A", "PC-B"
    a.mark_shared_run(now)
    b.mark_shared_run(now + timedelta(minutes=1))                # a second 3.8+ runner is fine
    ctl = book.tabs[ss.CONTROL_TAB]
    assert ctl.hidden and ctl.values[0][0].startswith("Running:") and ctl.values[0][2] == ss.SHARED_OWNER
    b.release_lock()
    assert ctl.values[0][0].startswith("Running:")              # left to go stale: A may still run
    # an OLD version's fresh lock blocks (it does not know claims); a stale one does not
    old_version_lock(book, now)
    with pytest.raises(ss.SheetError, match="older version"):
        a.mark_shared_run(now + timedelta(minutes=2))
    a.mark_shared_run(now + timedelta(minutes=6))


def test_pending_queue_retries_after_connection_returns(tmp_path):
    book = make_sheet(ROWS)
    src = source(book)
    src.download()
    q = ss.PendingQueue(tmp_path / "pending.json")
    q.add(src.id, src.tab, "a@x.com", 2, {"Mail Sent": "TRUE"})
    book.tabs["Customers"].offline = True
    assert len(q.flush(src)) == 1 and len(q.items()) == 1
    book.tabs["Customers"].offline = False
    assert q.flush(src) == [] and q.items() == []
    assert book.tabs["Customers"].cell(2, "Mail Sent") == "TRUE"


# ------------------------------------------------------------------ agent end to end
@pytest.fixture
def sheet_agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    key = key_file(tmp_path)

    def load(book, **env):
        real_connect = ss.SheetSource.connect

        def connect(self):
            self._client = FakeClient(book, SID)
            return real_connect(self)
        monkeypatch.setattr(ss.SheetSource, "connect", connect)
        base = {"CRISP_SOURCE": "sheet", "CRISP_SHEET_URL": URL, "CRISP_SHEET_TAB": "Customers",
                "CRISP_GOOGLE_KEY": str(key), "CRISP_DATA_DIR": str(tmp_path / "data"), "CRISP_CSV_FILE": "",
                "CRISP_REFERENCE_FILTER": "R", "CRISP_GUI_MODE": "1", "CRISP_DRY_RUN": "false",
                "CRISP_ROW_FROM": "", "CRISP_ROW_TO": "", "CRISP_DNC_FILE": "", "CRISP_SKIP_FAILED": "true",
                "CRISP_MAX_SKIPS": "5", "CRISP_MAX_FAILS_IN_ROW": "3",
                "CRISP_HISTORY_FILE": str(tmp_path / "history.csv"), "CRISP_RUN_DIR": "", "CRISP_DELAY": "0",
                "CRISP_RANDOM_DELAY": "false", "CRISP_MAX_PER_RUN": "", "CRISP_DAILY_CAP": "",
                "CRISP_SEND_WINDOW": "", "CRISP_START_AT": "", "CRISP_RETRY_ONLY": "false",
                "CRISP_HEALTH_CHECK": "false"}
        base.update(env)
        for k, v in base.items():
            monkeypatch.setenv(k, v)
        sys.modules.pop("crisp_email_agent", None)
        mod = importlib.import_module("crisp_email_agent")
        monkeypatch.setattr(mod, "sync_playwright", _FakePlaywright)
        monkeypatch.setattr(mod, "wait_for_crisp", lambda page: None)
        monkeypatch.setattr(mod, "recover_page", lambda page: True)
        monkeypatch.setattr(ss.SheetSource, "start_heartbeat", lambda self, interval=30: None)
        return mod
    yield load
    sys.modules.pop("crisp_email_agent", None)


def run(mod, capsys, sent_log, fail=()):
    def process(page, row):
        if row["Contact Email"] in fail:
            raise RuntimeError("Subject not found")
        sent_log.append(row["Contact Email"])
        return True
    mod.process_recipient = process
    mod.main()
    out = capsys.readouterr().out
    p = rs.RunParser(dry_run=False)
    p.feed(out.encode("utf-8"))
    return out, p.finish(0)


def test_live_sheet_run_ticks_mail_sent_and_writes_status(sheet_agent, capsys):
    book = make_sheet(ROWS)
    mod = sheet_agent(book)
    sent = []
    out, st = run(mod, capsys, sent, fail={"b@x.com"})
    ws = book.tabs["Customers"]
    assert sent == ["a@x.com", "c@x.com"] and st.completed == 2 and st.skipped == 1
    assert [ws.cell(r, "Mail Sent") for r in (2, 3, 4)] == ["TRUE", "FALSE", "TRUE"]
    assert [ws.cell(r, "Status") for r in (2, 3, 4)] == ["Sent", "Skipped", "Sent"]
    assert ws.cell(2, "Sent At") and "Subject not found" in ws.cell(3, "Error")
    assert "Sheet:     all results written." in out
    assert book.tabs[ss.CONTROL_TAB].values[0][2] == ss.SHARED_OWNER   # shared mode, no exclusive lock


def test_dropped_connection_never_causes_a_resend(sheet_agent, capsys, tmp_path):
    book = make_sheet(ROWS[:2])
    mod = sheet_agent(book)
    ws = book.tabs["Customers"]
    real_batch = ws.batch_update
    ws.batch_update = lambda *a, **k: (_ for _ in ()).throw(ConnectionError("network down"))
    sent = []
    out, st = run(mod, capsys, sent)
    assert sent == ["a@x.com", "b@x.com"]
    assert ws.cell(2, "Mail Sent") == "FALSE"                   # Sheet could not be updated...
    assert len(json.loads((tmp_path / "data" / "sheet_cache" / "pending.json").read_text())) >= 2

    ws.batch_update = real_batch                                 # ...connection back, run again
    mod = sheet_agent(book)
    sent2 = []
    out, st = run(mod, capsys, sent2)
    assert sent2 == []                                           # nobody emailed twice
    assert ws.cell(2, "Mail Sent") == "TRUE" and ws.cell(3, "Mail Sent") == "TRUE"


def test_an_old_app_version_running_blocks_a_live_run(sheet_agent, capsys):
    book = make_sheet(ROWS)
    old_version_lock(book, datetime.now(timezone.utc))
    mod = sheet_agent(book)
    sent = []
    with pytest.raises(ss.SheetError, match="older version"):
        run(mod, capsys, sent)
    assert sent == []


def test_dry_run_never_writes_to_the_sheet(sheet_agent, capsys):
    book = make_sheet(ROWS)
    mod = sheet_agent(book, CRISP_DRY_RUN="true")
    out, st = run(mod, capsys, [])
    assert st.completed == 3 and book.tabs["Customers"].writes == 0 and ss.CONTROL_TAB not in book.tabs


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body


def _api_error(code, body):
    e = FakeAPIError(code)
    e.response = _Resp(code, body)
    return e


def test_api_disabled_is_explained_and_not_retried():
    disabled = {"error": {"status": "PERMISSION_DENIED",
                          "message": "Google Sheets API has not been used in project 41 before or it is disabled.",
                          "details": [{"reason": "SERVICE_DISABLED",
                                       "metadata": {"activationUrl": "https://console.developers.google.com/x"}}]}}
    calls = []

    def open_sheet():
        calls.append(1)
        try:
            raise _api_error(403, disabled)
        except FakeAPIError as ex:
            raise PermissionError from ex               # exactly what gspread does
    p = ss.Pacer(interval=0)
    with pytest.raises(ss.SheetError, match="Sheets API is turned off.*console.developers.google.com/x"):
        p.call(open_sheet)
    assert calls == [1]                                 # a 403 is never retried


def test_not_shared_and_not_found_messages():
    p = ss.Pacer(interval=0)
    p.who = "bot@p.iam.gserviceaccount.com"
    with pytest.raises(ss.SheetError, match="Share > add bot@p.iam.gserviceaccount.com as Editor"):
        p.call(lambda: (_ for _ in ()).throw(_api_error(403, {"error": {"message": "caller lacks permission"}})))
    with pytest.raises(ss.SheetError, match="Sheet not found"):
        p.call(lambda: (_ for _ in ()).throw(_api_error(404, {})))
    assert ss.is_retryable(ConnectionError()) and not ss.is_retryable(PermissionError())

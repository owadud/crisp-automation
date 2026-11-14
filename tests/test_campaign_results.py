"""Campaign Results: read-only status checks against mock Crisp pages built from the
real Crisp markup, plus the results store rules. No real Crisp needed."""

import csv
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import campaign_results as cr  # noqa: E402
import send_control as sc  # noqa: E402
from crisp_conversation_mock import SCENARIOS, link  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 40)


# ------------------------------------------------------------------ reading real-markup pages
@pytest.fixture(scope="module")
def browser_page():
    pw_mod = pytest.importorskip("playwright.sync_api")
    with pw_mod.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()

        def serve(route):
            url = route.request.url
            if "session_" not in url:                      # icons etc.
                route.fulfill(status=204, body="")
                return
            name = url.split("session_")[1].split("-0000")[0]
            route.fulfill(body=SCENARIOS[name], content_type="text/html")
        page.route("https://app.crisp.chat/**", serve)
        yield page
        browser.close()


def read(page, name):
    page.goto(link(name))
    return page.evaluate(cr.READ_JS)


@pytest.mark.parametrize("name, status", [
    ("sent", cr.SENT), ("delivered", cr.DELIVERED), ("read", cr.READ), ("replied", cr.REPLIED),
    ("failed", cr.FAILED), ("no_time", cr.DELIVERED),
])
def test_each_status_from_a_mock_crisp_page(browser_page, name, status):
    raw = read(browser_page, name)
    result = cr.analyze(raw, "2026-11-05 03:40", NOW)
    assert result["problem"] == "" and result["status"] == status
    assert "customer@example.com" not in json.dumps(raw) and "offer" not in json.dumps(raw)   # no message text


def test_reply_time_and_open_state(browser_page):
    r = cr.analyze(read(browser_page, "replied"), "2026-11-05 03:40", NOW)
    assert (r["reply_at"], r["reply_kind"], r["open"]) == ("2026-11-05 11:55", "approx", True)
    r = cr.analyze(read(browser_page, "replied_resolved"), "2026-11-04 03:40", NOW)
    assert (r["reply_at"], r["reply_kind"], r["open"]) == ("2026-11-04 21:02", "exact", False)


def test_failed_reason_is_kept(browser_page):
    r = cr.analyze(read(browser_page, "failed"), "", NOW)
    assert r["status"] == cr.FAILED and r["reason"] == "Not delivered: mailbox full"


def test_lost_login_and_unknown_layout_are_detected(browser_page):
    assert cr.analyze(read(browser_page, "login"), "", NOW)["problem"] == "login_lost"
    assert cr.analyze(read(browser_page, "layout"), "", NOW)["problem"] == "layout"


# ------------------------------------------------------------------ agent check loop (read-only)
@pytest.fixture
def agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    for k, v in {"CRISP_CSV_FILE": str(tmp_path / "x.csv"), "CRISP_GUI_MODE": "1",
                 "CRISP_HISTORY_FILE": str(tmp_path / "h.csv")}.items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("crisp_email_agent", None)
    mod = importlib.import_module("crisp_email_agent")
    yield mod
    sys.modules.pop("crisp_email_agent", None)


def items(*names):
    return [{"key": f"c|{n}@x.com", "email": f"{n}@x.com", "link": link(n), "sent_at": "2026-11-05 03:40"}
            for n in names]


def test_check_loop_reads_every_page_and_never_interacts(agent, browser_page, tmp_path, capsys):
    clicks = []
    browser_page.on("request", lambda req: clicks.append(req.method) if req.method != "GET" else None)
    out = tmp_path / "observed.jsonl"
    n = agent.check_links(browser_page, items("sent", "read", "replied", "failed"), out, delay=0,
                          stop=lambda: False)
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert n == 4 and [x["result"]["status"] for x in lines] == ["Sent", "Read", "Replied", "Failed"]
    assert clicks == []                                       # only page loads, nothing posted
    assert "Status check: 4 of 4 conversations read." in capsys.readouterr().out


def test_check_stops_cleanly_on_lost_login(agent, browser_page, tmp_path, capsys):
    out = tmp_path / "observed.jsonl"
    n = agent.check_links(browser_page, items("read", "login", "sent"), out, delay=0, stop=lambda: False)
    text = capsys.readouterr().out
    assert n == 1 and "CHECK STOPPED: the Crisp login was lost" in text and "sent@x.com" not in text


def test_check_stops_cleanly_on_unknown_layout(agent, browser_page, tmp_path, capsys):
    out = tmp_path / "observed.jsonl"
    n = agent.check_links(browser_page, items("layout", "sent"), out, delay=0, stop=lambda: False)
    assert n == 0 and "layout was not recognised" in capsys.readouterr().out


# ------------------------------------------------------------------ store rules
def history_rows():
    return [
        {"Time": "2026-11-05 03:40:00", "Email": "a@x.com", "Name": "A", "Result": "Sent", "Mode": "live",
         "Campaign": "VIP", "Shortcut": "vip-offer", "Version": "v1", "Link": link("a")},
        {"Time": "2026-11-05 03:41:00", "Email": "b@x.com", "Name": "B", "Result": "Unconfirmed", "Mode": "live",
         "Campaign": "VIP", "Version": "v2", "Link": link("b")},
        {"Time": "2026-11-05 03:42:00", "Email": "c@x.com", "Result": "Sent", "Mode": "dry", "Campaign": "VIP"},
        {"Time": "2026-11-01 10:00:00", "Email": "d@x.com", "Result": "Sent", "Mode": "live", "Subject": "Old"},
    ]


def test_store_never_downgrades_and_labels_first_seen(tmp_path):
    st = cr.ResultsStore(tmp_path / "r.json")
    assert st.sync_history(history_rows()) == 3                  # dry run never counts
    key = cr.rkey("VIP", "a@x.com")
    st.apply(key, {"status": cr.READ, "problem": ""}, NOW)
    st.apply(key, {"status": cr.DELIVERED, "problem": ""}, NOW)  # later page shows less: keep Read
    rec = st.recipients[key]
    assert rec["status"] == cr.READ and rec["delivered_seen"] and rec["read_seen"]
    row = cr.table_row(rec)
    assert row[4] == "2026-11-05 12:40 (first seen)" and row[5] == "2026-11-05 12:40 (first seen)"
    st.apply(key, {"status": cr.REPLIED, "reply_at": "2026-11-05 11:55", "reply_kind": "exact", "open": True,
                   "problem": ""}, NOW)
    assert rec["status"] == cr.REPLIED and rec["reply_at"] == "2026-11-05 11:55" and not rec["reply_seen"]
    st.apply(key, {"problem": "layout"}, NOW)
    assert rec["status"] == cr.REPLIED                           # a bad page never changes a status
    st.save()
    assert cr.ResultsStore(tmp_path / "r.json").recipients[key]["status"] == cr.REPLIED


def test_missing_reply_time_uses_first_seen(tmp_path):
    st = cr.ResultsStore(tmp_path / "r.json")
    st.sync_history(history_rows())
    key = cr.rkey("VIP", "b@x.com")
    st.apply(key, {"status": cr.REPLIED, "reply_at": "", "problem": ""}, NOW)
    assert cr.table_row(st.recipients[key])[6] == "2026-11-05 12:40 (first seen)"


def test_summary_compare_suggestions_history_and_export(tmp_path):
    st = cr.ResultsStore(tmp_path / "r.json")
    st.sync_history(history_rows())
    st.apply(cr.rkey("VIP", "a@x.com"), {"status": cr.REPLIED, "reply_at": "", "problem": ""}, NOW)
    st.apply(cr.rkey("VIP", "b@x.com"), {"status": cr.FAILED, "reason": "bounced", "problem": ""}, NOW)
    s = cr.summary(st.rows("VIP"))
    assert (s["sent"], s["delivered"], s["read"], s["replied"], s["failed"]) == (2, 1, 1, 1, 1)
    assert s["replied_pct"] == 50.0
    assert cr.summary_text(s).startswith("2 sent · 1 delivered (50.0%)")
    versions = {g["version"]: g["replied_pct"] for g in cr.compare(st.rows("VIP"), "version")}
    assert versions == {"v1": 100.0, "v2": 0.0}
    assert st.campaigns() == ["VIP", "Old"]
    assert cr.recheck_suggestions(st.rows("VIP"), "", datetime(2026, 11, 6, 9, 0)) == ["1 day"]
    assert cr.recheck_suggestions(st.rows("VIP"), "2026-11-06 08:00", datetime(2026, 11, 6, 9, 0)) == []
    snap = st.record_check("VIP", NOW)
    assert snap["replied"] == 1 and st.history("vip")[0]["at"] == "2026-11-05 12:40"
    path = cr.export_csv(st.rows("VIP"), tmp_path / "out.csv")
    rows = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert rows[0] == cr.TABLE_COLUMNS and len(rows) == 3


def test_history_records_link_shortcut_and_version(tmp_path):
    h = tmp_path / "history.csv"
    sc.append_history(h, Email="A@x.com", Result="Sent", Mode="live", Campaign="VIP", Shortcut="vip",
                      Version="v2", Link=link("a"))
    row = sc.read_history(h)[0]
    assert (row["Shortcut"], row["Version"], row["Link"]) == ("vip", "v2", link("a"))


def test_version_comparison_shows_shortcut_and_subject():
    rows = [{"version": "v1", "shortcut": "!a", "subject": "Free plugin", "status": "sent"},
            {"version": "v2", "shortcut": "!a", "subject": "Offer", "status": "sent"}]
    g = {x["version"]: x for x in cr.compare(rows, "version")}
    assert (g["v1"]["shortcuts"], g["v1"]["subjects"]) == ("!a", "Free plugin") and g["v2"]["subjects"] == "Offer"

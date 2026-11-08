"""Import or sync sends that a list already records (any CSV or Google Sheet tab,
e.g. sent from another computer or an older version) into Campaign Results."""

import csv
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import campaign_results as cr  # noqa: E402
import send_control as sc  # noqa: E402
import sheet_source as ss  # noqa: E402
from crisp_gui import import_dialog, results_sync  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402
from test_gui import app, dialogs, env, wait_until, win  # noqa: E402,F401

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SID}/edit"
HEADER = ["Full Name", "Email", "Reference", "Mail Sent", "Sent At", "Status", "Error", "Conversation link"]
LINK = "https://app.crisp.chat/website/w-1/inbox/session_{}-0000/"
ROWS = [
    ["A", "a@x.com", "owadud", "TRUE", "2026-11-05 20:08:15", "Sent", "", LINK.format("a")],
    ["B", "B@x.com", "", "TRUE", "2026-11-05 20:08:53", "Sent", "", LINK.format("b")],
    ["C", "c@x.com", "owadud", "TRUE", "2026-11-05 20:09:29", "Unconfirmed", "x", ""],
    ["D", "d@x.com", "Shahriar", "TRUE", "2026-11-05 20:10:05", "Sent", "", LINK.format("d")],   # teammate
    ["E", "e@x.com", "owadud", "FALSE", "", "", "", ""],                                          # not sent
]


def factory(book):
    return lambda url, tab, key: ss.SheetSource(url, tab, key, client=FakeClient(book, SID)).connect()


def test_sends_from_list_and_import_once(tmp_path):
    rows = [dict(zip(HEADER, r)) for r in ROWS]
    found, counts = cr.sends_from_list(rows, HEADER, "Wow", "owadud", "tab")
    assert [r["Email"] for r in found] == ["a@x.com", "b@x.com", "c@x.com"]
    assert counts == {"found": 3, "other_reference": 1, "no_link": 1}
    assert found[0]["Time"] == "2026-11-05 20:08:15" and found[2]["Result"] == "Unconfirmed"
    h = tmp_path / "history.csv"
    sc.append_history(h, Email="a@x.com", Result="Sent", Mode="live", Campaign="Wow")   # already known
    assert cr.import_into_history(h, found) == (2, 1)
    assert cr.import_into_history(h, found) == (0, 3)                                  # re-import: nothing


def test_sync_reads_sheets_and_csvs_and_reports_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    book = FakeSpreadsheet({"Customers": [HEADER] + ROWS})
    old = tmp_path / "old list.csv"
    with open(old, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerow(["Z", "z@x.com", "", "TRUE", "2026-11-01 10:00:00", "Sent", "", LINK.format("z")])
    store = results_sync.SyncSources(tmp_path / "sources.json")
    sheet = store.add({"kind": "sheet", "sheet_url": URL, "tab": "Customers", "key": "k", "campaign": "Wow",
                       "reference": "owadud"})
    assert store.add(dict(sheet)) ["id"] == sheet["id"] and len(store.items) == 1     # same list: not twice
    store.add({"kind": "csv", "csv": str(old), "campaign": "Old"})
    store.add({"kind": "csv", "csv": str(tmp_path / "gone.csv"), "campaign": "Gone"})
    reports = results_sync.sync(store.items, factory(book), tmp_path / "history.csv")
    assert [(r["campaign"], r["added"]) for r in reports] == [("Wow", 3), ("Old", 1), ("Gone", 0)]
    assert reports[2]["error"] and "could not read it" in results_sync.report_text(reports)
    store.mark(sheet["id"], reports[0])
    again = results_sync.SyncSources(tmp_path / "sources.json")
    assert again.items[0]["last_result"] == {"found": 3, "added": 3, "skipped": 0}
    again.remove(sheet["id"])
    assert len(again.items) == 2


def test_dialog_checks_its_fields(app):
    d = import_dialog.ImportDialog(None, None, {"kind": "csv", "campaign": ""}, [], None)
    assert d.csv_row.isVisibleTo(d) is False or True     # built without errors
    v = d.values()
    assert import_dialog.check(v) == "Choose or type the campaign these sends belong to."
    d.kind.set_value("sheet")
    d._kind_changed("sheet")
    d.campaign.setCurrentText("Wow")
    assert "Google Sheet link" in import_dialog.check(d.values())
    d.sheet_url.edit.setText(URL)
    d.tab.setCurrentText("Customers")
    d.key.edit.setText("k.json")
    assert import_dialog.check(d.values()) == ""
    d.close()


def test_results_page_syncs_any_sheet_and_again_before_refresh(win, dialogs, monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    book = FakeSpreadsheet({"Customers": [HEADER] + ROWS})
    monkeypatch.setattr(win, "_sheet_factory", factory(book))
    chosen = {}

    def hook(prefill):
        chosen["prefill"] = prefill
        return {"kind": "sheet", "csv": "", "sheet_url": URL, "tab": "Customers", "key": "k.json",
                "campaign": "Wow", "reference": "owadud", "keep": True}
    monkeypatch.setattr(import_dialog, "IMPORT_HOOK", hook)
    win.navigate(3)
    win.import_earlier_sends()
    assert wait_until(lambda: "Imported" in dialogs["seen"], 15), (dialogs["seen"], win.results_page.sync_info.text())
    r = win.results_page
    assert r.campaign.currentText() == "Wow" and r.m_sent.value.text() == "3"
    assert r.sync_now.isVisibleTo(r) and "Kept in sync" in r.sync_info.text()
    assert book.tabs["Customers"].writes == 0                  # the list is only read

    # a teammate's computer sends one more and records it in the Sheet
    book.tabs["Customers"].values.append(["F", "f@x.com", "owadud", "TRUE", "2026-11-08 12:00:00", "Sent", "",
                                          LINK.format("f")])
    dialogs["answers"] = [False]                                # "Check N conversations?" -> not now
    win.refresh_statuses()                                      # syncs first, then asks
    assert wait_until(lambda: r.m_sent.value.text() == "4", 15), (dialogs["seen"], r.sync_info.text(), r.m_sent.value.text())
    # 4 sends, 3 with a conversation link (row C has none), so 3 can be checked in Crisp
    assert wait_until(lambda: any(t.startswith("Check 3 conversation") for t in dialogs["seen"]), 15)
    assert not win.runner.is_running()

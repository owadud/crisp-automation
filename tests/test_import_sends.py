"""Import earlier sends that a list already records (e.g. sent from another computer
or an older version) so Campaign Results can check them."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import campaign_results as cr  # noqa: E402
import send_control as sc  # noqa: E402
import sheet_source as ss  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402
from test_gui import app, dialogs, env, wait_until, win  # noqa: E402,F401

HEADER = ["Full Name", "Email", "Reference", "Mail Sent", "Sent At", "Status", "Error", "Conversation link"]
LINK = "https://app.crisp.chat/website/w-1/inbox/session_{}-0000/"
ROWS = [
    ["A", "a@x.com", "owadud", "TRUE", "2026-11-05 20:08:15", "Sent", "", LINK.format("a")],
    ["B", "B@x.com", "", "TRUE", "2026-11-05 20:08:53", "Sent", "", LINK.format("b")],
    ["C", "c@x.com", "owadud", "TRUE", "2026-11-05 20:09:29", "Unconfirmed", "x", ""],
    ["D", "d@x.com", "Shahriar", "TRUE", "2026-11-05 20:10:05", "Sent", "", LINK.format("d")],   # teammate
    ["E", "e@x.com", "owadud", "FALSE", "", "", "", ""],                                          # not sent
]


def test_sends_from_list_and_import_once(tmp_path):
    rows = [dict(zip(HEADER, r)) for r in ROWS]
    found, counts = cr.sends_from_list(rows, HEADER, "Wow", "owadud", "tab")
    assert [r["Email"] for r in found] == ["a@x.com", "b@x.com", "c@x.com"]
    assert counts == {"found": 3, "other_reference": 1, "no_link": 1}
    assert found[0]["Time"] == "2026-11-05 20:08:15" and found[0]["Link"] == LINK.format("a")
    assert found[2]["Result"] == "Unconfirmed"
    h = tmp_path / "history.csv"
    sc.append_history(h, Email="a@x.com", Result="Sent", Mode="live", Campaign="Wow")   # already known
    assert cr.import_into_history(h, found) == (2, 1)
    assert cr.import_into_history(h, found) == (0, 3)                                  # re-import: nothing
    st = cr.ResultsStore(tmp_path / "r.json")
    st.sync_history(sc.read_history(h))
    rows = {r["email"]: r for r in st.rows("Wow")}
    assert rows["b@x.com"]["link"] == LINK.format("b") and rows["b@x.com"]["sent_at"].startswith("2026-11-05")


def test_results_page_imports_from_the_open_sheet(win, dialogs, monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    book = FakeSpreadsheet({"Customers": [HEADER] + ROWS})
    monkeypatch.setattr(win, "_sheet_factory", lambda url, tab, key: ss.SheetSource(
        url, tab, key, client=FakeClient(book, sid)).connect())
    win._apply_campaign_values({"source": "sheet", "sheet_url": f"https://docs.google.com/spreadsheets/d/{sid}/",
                                "sheet_tab": "Customers", "google_key": "k.json", "campaign": "Wow",
                                "reference": "owadud"})
    win.navigate(3)
    win.import_earlier_sends()
    assert wait_until(lambda: "Imported" in dialogs["seen"], 15)
    assert "Import into campaign “Wow”?" in dialogs["seen"]
    assert win.results_page.campaign.currentText() == "Wow"
    assert win.results_page.m_sent.value.text() == "3"
    assert book.tabs["Customers"].writes == 0                  # the list is only read

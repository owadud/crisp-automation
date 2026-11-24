"""Fill plugins from WPXPO: reading the account's answer, the columns, many people, writing a CSV.
All customers here are made up."""

import csv
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import personal as pz  # noqa: E402
import wpxpo_account as wa  # noqa: E402

K1, K2, K3 = "a" * 32, "b" * 32, "c" * 32
PAGE = f"""<div><strong>ID:</strong> #100</div><div><strong>Email:</strong> ann@shop.test</div>
<div><strong>Product Name:</strong> WowStore Pro – Lifetime Unlimited Sites (complete) - Lifetime</div>
<div><strong>Date:</strong> September 11, 2024</div>
<div><strong>License:</strong> {K1} (active)[3/Unlimited]</div>
<div><strong>Active Sites:</strong> one.test/<br>two.test/</div><div><strong>Price:</strong> $ 150</div>
<div><strong>ID:</strong> #101</div>
<div><strong>Product Name:</strong> WholesaleX Pro – Yearly 20 Sites (complete)</div>
<div><strong>Date:</strong> October 23, 2024</div>
<div><strong>License:</strong> {K2} (active)[20/20]</div><div><strong>Price:</strong> $ 280.50</div>
<div><strong>ID:</strong> #102</div>
<div><strong>Product Name:</strong> WowRevenue Pro – Yearly 1 Site (complete)</div>
<div><strong>Date:</strong> April 17, 2025</div>
<div><strong>License:</strong> {K3} (expired)[1/1]</div><div><strong>Price:</strong> $ 59</div>"""


def test_orders_are_read_from_the_accounts_html():
    orders = wa.parse_orders(PAGE)
    assert [o["plugin"] for o in orders] == ["WowStore", "WholesaleX", "WowRevenue"]
    assert orders[0]["plan"] == "Lifetime" and orders[0]["date"] == "2024-09-11" and orders[0]["price"] == 150
    assert orders[0]["sites"] == ["one.test/", "two.test/"]
    assert orders[1]["licenses"] == [{"status": "active", "used": "20", "limit": "20"}]


def test_columns_for_one_person():
    cols = wa.summarize(wa.parse_orders(PAGE), today="2026-11-24")
    assert cols["Pro Plugins"] == "WowStore, WholesaleX" and cols["Plugin Count"] == "2"     # expired: not owned
    assert cols["Plan"] == "Mixed" and cols["Active Licenses"] == "2" and cols["Expired Licenses"] == "1"
    assert cols["Expired Plugins"] == "WowRevenue" and cols["Near Site Limit"] == "WholesaleX (20/20)"
    assert cols["First Purchase"] == "2024-09-11" and cols["Last Purchase"] == "2025-04-17"
    assert cols["Account Total"] == "$489.50" and cols[wa.CHECKED] == "2026-11-24"
    assert "WowStore · Lifetime · active · 3/Unlimited sites" in cols["Plugin Details"]
    assert cols[wa.REMAINING] == "Expired: WowRevenue"                          # its own column...
    assert cols[wa.NOT_BOUGHT] == ("PostX, WowAddons, WowShipping, WowOptin, "    # ...apart from never ordered
                                   "WowInvoice, WowRecommend, WowSubscription")
    empty = wa.summarize([], today="2026-11-24")
    assert empty[wa.CHECKED] == "2026-11-24 · no orders" and empty[wa.REMAINING] == ""
    assert empty[wa.NOT_BOUGHT].startswith("PostX, WowStore")


def test_refunded_pending_and_abandoned_orders_are_not_owned():
    page = PAGE.replace("WholesaleX Pro – Yearly 20 Sites (complete)", "WholesaleX Pro – Yearly 20 Sites (refunded)")
    page += f"""<div><strong>ID:</strong> #103</div>
<div><strong>Product Name:</strong> WowOptin Pro – Yearly 1 Site (pending)</div>
<div><strong>Date:</strong> May 1, 2025</div><div><strong>Price:</strong> $ 49</div>
<div><strong>ID:</strong> #104</div>
<div><strong>Product Name:</strong> ProductX Pro – Yearly 1 Site (abandoned)</div>
<div><strong>Date:</strong> May 2, 2025</div><div><strong>Price:</strong> $ 49</div>"""
    orders = wa.parse_orders(page)
    assert [o["status"] for o in orders] == ["complete", "refunded", "complete", "pending", "abandoned"]
    assert orders[4]["plugin"] == "WowStore"                                     # old name ProductX
    cols = wa.summarize(orders, today="2026-11-24")
    assert cols["Pro Plugins"] == "WowStore" and cols["Account Total"] == "$209" and cols["Account Orders"] == "2"
    rem = cols[wa.REMAINING]
    assert rem == "Expired: WowRevenue · Refunded: WholesaleX · Pending: WowOptin"
    assert cols[wa.NOT_BOUGHT].startswith("PostX, WowAddons") and "WowOptin" not in cols[wa.NOT_BOUGHT]
    assert "WowStore" not in rem                       # owned: an abandoned second cart does not matter


def test_a_bundle_order_owns_all_ten():
    page = """<div><strong>ID:</strong> #1</div><div><strong>Product Name:</strong> WPXPO Bundle – Lifetime
 Unlimited Sites (complete)</div><div><strong>Date:</strong> June 1, 2025</div>
<div><strong>License:</strong> """ + "d" * 32 + """ (active)[1/Unlimited]</div><div><strong>Price:</strong> $ 599</div>"""
    cols = wa.summarize(wa.parse_orders(page), today="2026-11-24")
    assert cols["Plugin Count"] == "10" and cols[wa.REMAINING] == cols[wa.NOT_BOUGHT] == ""


def test_many_people_with_pauses_batches_and_a_bad_key():
    answers = {"ann@shop.test": {"success": True, "message": PAGE},
               "bo@x.test": {"success": True, "message": "<h3>No Data Found.</h3>"}}
    batches, pauses = [], []
    out, problems = wa.fill(["Ann@Shop.test", "bo@x.test", "ann@shop.test"], "k",
                            post=lambda body: answers[body["email"]], sleep=pauses.append,
                            on_batch=batches.append, batch=1)
    assert list(out) == ["ann@shop.test", "bo@x.test"] and problems == []
    assert len(batches) == 2 and pauses == [wa.PAUSE]
    refused = {"success": False, "message": "Invalid license Key or Emails.kx"}
    out, problems = wa.fill(["a@x.test", "b@x.test"], "bad", post=lambda body: refused, sleep=lambda s: None)
    assert out == {} and "refused the API key" in problems[0] and "kx" not in problems[0]


def test_csv_gets_the_columns_and_keeps_everything_else(tmp_path):
    path = tmp_path / "list.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([["Full Name", "Email", "User List", "", "Status"],
                                 ["Ann", "ann@shop.test", "WooCommerce", "WowStore Users", "Subscribed"],
                                 ["Bo", "bo@x.test", "", "", ""]])
    cols = wa.summarize(wa.parse_orders(PAGE), today="2026-11-24")
    assert wa.write_csv(path, {"ann@shop.test": cols}) == 1
    grid = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert grid[0][:5] == ["Full Name", "Email", "User List", "", "Status"] and grid[0][5:] == wa.COLUMNS
    assert grid[1][3] == "WowStore Users" and grid[1][5] == "WowStore, WholesaleX"
    info = pz.build_index(grid)[0]["ann@shop.test"]
    assert pz.plugins_of(info)[:2] == ["WowStore", "WholesaleX"]      # {plugins} uses them


def test_csv_columns_from_an_older_version_are_moved_together(tmp_path):
    path = tmp_path / "list.csv"
    old = ["Email", "Pro Plugins", "Plan", "Notes", "WPXPO Checked"]       # v3.36 order, a user column between
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([old, ["bo@x.test", "PostX", "Yearly", "call him", "2026-01-01"]])
    wa.write_csv(path, {})
    grid = list(csv.reader(path.open(encoding="utf-8-sig")))
    assert grid[0] == ["Email", "Notes"] + wa.COLUMNS
    row = dict(zip(grid[0], grid[1]))
    assert row["Notes"] == "call him" and row["Pro Plugins"] == "PostX" and row[wa.CHECKED] == "2026-01-01"


def test_sheet_block_is_moved_together_and_tinted():
    import sheet_source as ss

    class WS:
        id = 7

    class SH:
        def __init__(self):
            self.calls = []

        def batch_update(self, body):
            self.calls.append(body["requests"])

    src = ss.SheetSource.__new__(ss.SheetSource)
    src.header, src.colmap, src.data_rows = ["Email", "Pro Plugins", "Notes", "Plan"], {}, 1
    src.ws, src.sh = WS(), SH()
    src.pacer = type("P", (), {"call": staticmethod(lambda fn, *a, **k: fn(*a, **k))})()
    src.ensure_columns = lambda cols: [src.header.append(c) for c in cols if c not in src.header]
    assert src.arrange_block(["Pro Plugins", "Plan", "Checked"], notes={"Plan": "hi"})
    assert src.header == ["Email", "Notes", "Pro Plugins", "Plan", "Checked"]
    moves, fmt = src.sh.calls
    assert [m["moveDimension"]["source"]["startIndex"] for m in moves] == [1, 2, 2]
    assert fmt[0]["repeatCell"]["range"]["startColumnIndex"] == 2 and "note" in str(fmt[1])
    src.sh.calls.clear()
    assert not src.arrange_block(["Pro Plugins", "Plan", "Checked"]) and len(src.sh.calls) == 1   # only format


def test_recently_checked_people_are_skipped():
    from datetime import datetime
    now = datetime(2026, 11, 24)
    assert wa.checked_recently("2026-11-10", today=now) and not wa.checked_recently("2026-09-01", today=now)
    assert not wa.checked_recently("error: timeout", today=now)


def test_lists_button_fills_a_csv_list(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui import widgets as W
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CRISP_WPXPO_CONFIG", str(tmp_path / "wpxpo.json"))
    monkeypatch.setenv("CRISP_MAIL_CONFIG", str(tmp_path / "mail.json"))
    monkeypatch.setattr(W, "DIALOG_HOOK", lambda *a: True)
    monkeypatch.setattr(wa, "PAUSE", 0)
    monkeypatch.setattr(wa, "lookup", lambda email, key, post=None: wa.parse_orders(PAGE) if email == "u0@example.com"
                        else [])
    wa.save_key("test-key")
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    path = tg.make_csv(tmp_path / "list.csv", n=2)
    prefs.set_campaign_value("csv", str(path))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    try:
        w.refresh_csv()
        w.fill_plugins_from_wpxpo()
        for _ in range(300):
            app.processEvents()
            if not w._wpx_busy:
                break
            import time
            time.sleep(0.01)
        assert "with plugins" in w.campaign.fill_status.text()
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert rows[0]["Pro Plugins"] == "WowStore, WholesaleX" and "Expired: WowRevenue" in rows[0][wa.REMAINING]
        assert "no orders" in rows[1][wa.CHECKED]
    finally:
        w.close()


def test_new_columns_widen_a_full_sheet_tab_first():
    """A tab with only A-Z (26 columns) used up: the WPXPO columns need more, so columns are added first."""
    import sheet_source as ss

    class WS:
        col_count = 26

        def __init__(self):
            self.calls = []

        def add_cols(self, n):
            self.calls.append(("add_cols", n))
            self.col_count += n

        def update(self, values=None, range_name="A1", value_input_option="RAW"):
            assert self.col_count >= 26 + len(values[0]), "wrote past the grid"
            self.calls.append(("update", range_name))

    src = ss.SheetSource.__new__(ss.SheetSource)
    src.header, src.colmap, src.data_rows = [f"C{i}" for i in range(26)], {}, 0
    src.ws = WS()
    src.pacer = type("P", (), {"call": staticmethod(lambda fn, *a, **k: fn(*a, **k))})()
    src.ensure_columns(wa.COLUMNS)
    assert src.ws.calls == [("add_cols", len(wa.COLUMNS)), ("update", "AA1")]


def test_message_using_plugin_columns_on_an_unfilled_list_says_how_to_fill_it():
    import personal as pz
    text = "{if Not Bought Plugins}Try {Not Bought Plugins}.{endif}"
    problems = pz.check([text], ["Full Name", "Contact Email"])
    assert any("Fill plugins from WPXPO" in p and p.startswith("Not Bought Plugins") for p in problems)
    assert pz.check([text], ["Full Name", "Contact Email", wa.NOT_BOUGHT]) == []

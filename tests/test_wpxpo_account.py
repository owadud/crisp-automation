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
    assert cols["Pro Plugins"] == "WowStore, WholesaleX, WowRevenue" and cols["Plugin Count"] == "3"
    assert cols["Plan"] == "Mixed" and cols["Active Licenses"] == "2" and cols["Expired Licenses"] == "1"
    assert cols["Expired Plugins"] == "WowRevenue" and cols["Near Site Limit"] == "WholesaleX (20/20)"
    assert cols["First Purchase"] == "2024-09-11" and cols["Last Purchase"] == "2025-04-17"
    assert cols["Account Total"] == "$489.50" and cols[wa.CHECKED] == "2026-11-24"
    assert "WowStore · Lifetime · active · 3/Unlimited sites" in cols["Plugin Details"]
    assert wa.summarize([], today="2026-11-24")[wa.CHECKED] == "2026-11-24 · no orders"


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
    assert grid[1][3] == "WowStore Users" and grid[1][5] == "WowStore, WholesaleX, WowRevenue"
    info = pz.build_index(grid)[0]["ann@shop.test"]
    assert pz.plugins_of(info)[:3] == ["WowStore", "WholesaleX", "WowRevenue"]      # {plugins} uses them


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
        assert "with plugins" in w.lists_page.fill_status.text()
        rows = list(csv.DictReader(path.open(encoding="utf-8-sig")))
        assert rows[0]["Pro Plugins"] == "WowStore, WholesaleX, WowRevenue"
        assert "no orders" in rows[1][wa.CHECKED]
    finally:
        w.close()

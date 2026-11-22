"""Read tracking for My email (this computer only): invisible image, tracked links, the
Apps Script tracker's list, and Results showing who opened and when."""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import campaign_results as cr  # noqa: E402
import mail_sender as ms  # noqa: E402
import send_control as sc  # noqa: E402
import tracking  # noqa: E402
from test_gui import app, dialogs, env, win  # noqa: E402,F401

URL = "https://script.google.com/macros/s/AKfycbxTEST123/exec"
CFG = {"provider": "gmail", "email": "me@example.com", "password": "p", "from_name": "Me", "host": "smtp.gmail.com",
       "port": 465, "security": "ssl", "unsubscribe": True, "track": True, "track_url": URL, "track_key": "k1"}


def test_tracking_is_off_unless_fully_set_up():
    assert tracking.configured(CFG)
    assert not tracking.configured(dict(CFG, track=False))
    assert not tracking.configured(dict(CFG, track_url="https://example.com/x"))
    assert not tracking.configured(dict(CFG, track_key=""))
    assert "KEY = 'abc'" in tracking.script_for("abc") and "__KEY__" not in tracking.script_for("abc")


def test_email_gets_the_image_and_only_wanted_links_are_tracked():
    body = "Hi Ann,\n\n[See the deal](https://x.com/deal?a=1&b=2)"
    msg = ms.build_message(CFG, "ann@x.com", "Offer", body, track_id="ID1")
    plain, page = [p.get_content() for p in msg.iter_parts()]
    assert f'<img src="{URL}?i=ID1"' in page and page.count("<img") == 1
    assert 'href="https://x.com/deal?a=1&amp;b=2"' in page or 'href="https://x.com/deal?a=1&b=2"' in page   # opens only
    assert "script.google.com" not in plain                                   # text part untouched
    clicks = ms.build_message(dict(CFG, track_clicks=True), "ann@x.com", "Offer", body, track_id="ID1")
    page = list(clicks.iter_parts())[1].get_content()
    assert f'href="{URL}?i=ID1&u=https%3A%2F%2Fx.com%2Fdeal%3Fa%3D1%26b%3D2"' in page
    off = ms.build_message(dict(CFG, track=False), "ann@x.com", "Offer", body, track_id="ID1")
    assert "script.google.com" not in list(off.iter_parts())[1].get_content()  # switched off: nothing added
    html_page = '<html><body><p>Hi</p><a href="mailto:me@x.com">Mail me</a></body></html>'
    out = tracking.add_tracking(html_page, URL, "ID2", links=True)
    assert 'href="mailto:me@x.com"' in out and out.index("?i=ID2") < out.index("</body>")


def test_events_become_opens_times_and_clicks(tmp_path):
    tracking.record_sent(tmp_path, "2026-11-17 10:00:00", "A1", "Ann@X.com", "Ann", "Promo", "Hi")
    tracking.record_sent(tmp_path, "2026-11-17 10:01:00", "B2", "bo@x.com", "Bo", "Promo", "Hi")
    rows = [["2026-11-17T04:10:00.000Z", "open", "A1", ""], ["2026-11-17T06:30:00.000Z", "open", "A1", ""],
            ["2026-11-17T06:31:00.000Z", "click", "A1", "https://x.com"], ["2026-11-17T05:00:00.000Z", "open", "ZZ", ""]]
    seen = []

    def get(url):
        seen.append(url)
        return "ok" if "ping=1" in url else json.dumps(rows)
    assert tracking.ping(CFG, get) == "" and "key=k1" in seen[0]
    summ = tracking.summarize(tracking.fetch_events(CFG, get), tracking.read_ledger(tmp_path))
    s = summ[("Promo", "ann@x.com")]
    assert (s["opens"], s["clicks"], s["links"]) == (2, 1, ["https://x.com"])
    assert s["first_open"] < s["last_open"] <= s["last_click"]
    assert ("Promo", "bo@x.com") not in summ                                  # not opened: nothing
    assert tracking.summary_text(s).startswith("opened 2×")
    assert "different key" in tracking.ping(CFG, lambda url: "wrong key")
    assert "web app address" in tracking.ping(dict(CFG, track_url="nope"), get)


def test_results_show_read_time_from_the_tracker(tmp_path):
    st = cr.ResultsStore(tmp_path / "r.json")
    st.sync_history([{"Time": "2026-11-17 10:00:00", "Email": "ann@x.com", "Result": "Sent", "Mode": "live",
                      "Campaign": "Promo"}])
    key = cr.rkey("Promo", "ann@x.com")
    s = {"opens": 2, "first_open": "2026-11-17 10:10", "last_open": "2026-11-17 12:30", "clicks": 1,
         "last_click": "2026-11-17 12:31", "note": "opened 2×, last 12:30 · 1 click, last 12:31"}
    assert st.apply_tracking(key, s) and not st.apply_tracking(key, s)       # second time: nothing new
    rec = st.recipients[key]
    assert rec["status"] == cr.READ and rec["read_at"] == "2026-11-17 10:10"
    row = cr.table_row(rec)
    assert row[5] == "2026-11-17 10:10" and "(first seen)" not in row[5] and "opened 2×" in row[-1]


def test_settings_setup_script_test_and_results_sync(win, dialogs, monkeypatch, tmp_path):
    from PySide6.QtGui import QGuiApplication
    s = win.settings
    win.mail_config_path = tmp_path / "mail_account.json"
    ms.save_config(dict(CFG, track=False, track_url="", track_key=""), win.mail_config_path)
    win._load_mail_fields()
    assert not s.mail_track.isChecked()
    win.copy_tracking_script()
    key = ms.load_config(win.mail_config_path)["track_key"]
    assert key and f"KEY = '{key}'" in QGuiApplication.clipboard().text()
    win.copy_tracking_script()                                               # the key never changes by itself
    assert ms.load_config(win.mail_config_path)["track_key"] == key
    s.mail_track.setChecked(True)
    monkeypatch.setattr(win, "_mail_factory", lambda cfg: (_ for _ in ()).throw(AssertionError("no send")))
    win.save_mail_account()                                                  # ticked but no address: refused
    assert "not set up" in s.mail_status.text()
    s.mail_track_url.edit.setText(URL)
    form = win._mail_form()
    assert tracking.configured(form) and form["track_key"] == key
    # Results: an open from the tracker turns a My email send into Read, with the exact time
    sc.append_history(win.history_file, Time="2026-11-17 10:00:00", Email="ann@x.com", Result="Sent", Mode="live",
                      Campaign="Promo")
    tracking.record_sent(win.data_dir, "2026-11-17 10:00:00", "A1", "ann@x.com", "Ann", "Promo", "Hi")
    win._tracking_done("sync", {("Promo", "ann@x.com"): {"opens": 1, "first_open": "2026-11-17 10:10",
                                                         "last_open": "2026-11-17 10:10", "clicks": 0,
                                                         "last_click": "", "links": [], "note": "opened 1×"}}, None)
    rec = win.results.recipients[cr.rkey("Promo", "ann@x.com")]
    assert rec["status"] == cr.READ and rec["read_at"] == "2026-11-17 10:10"
    assert "1 of your emails opened" in win.results_page.progress_text.text()


# ------------------------------------------------------------------ one tracker, several computers
URL2 = "https://script.google.com/macros/s/AKfycbAbc123_-x/exec"


def test_link_for_another_computer_carries_the_key():
    cfg = {"track_url": URL2, "track_key": "k3y-abc"}
    link = tracking.share_link(cfg)
    assert tracking.split_link(link) == (URL2, "k3y-abc") and tracking.valid_url(link)
    assert tracking.valid_url("https://script.google.com/a/macros/wpxpo.com/s/AKfyc_1/exec")   # Workspace


def test_ping_says_what_is_really_wrong():
    cfg = {"track_url": URL2, "track_key": "k"}
    assert "no tracker key" in tracking.ping({"track_url": URL2, "track_key": ""})
    assert "Copy for another computer" in tracking.ping(cfg, get=lambda u: "wrong key")
    assert tracking.ping(cfg, get=lambda u: "ok") == ""

    def cert(u):
        raise OSError("<urlopen error [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed>")
    assert "Install Certificates" in tracking.ping(cfg, get=cert)
    assert "Who has access" in tracking.ping(cfg, get=lambda u: "<html><title>Sign in</title>accounts.google</html>")

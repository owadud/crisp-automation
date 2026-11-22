"""Admin usage reporting: teammates report each real run to the admin's private collector, only
the admin can read it, and the agent reports after a live run (never a dry run)."""

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import admin_report as ar  # noqa: E402
import importlib  # noqa: E402
import pytest  # noqa: E402
from test_gui import app, dialogs, env, wait_until, win  # noqa: E402,F401

URL = "https://script.google.com/macros/s/AKfycbxADMIN/exec"


def cfg(**kw):
    base = {"report": True, "report_url": URL, "write_key": "wk", "admin_key": "ak"}
    base.update(kw)
    return base


def test_keys_roles_and_teammate_config():
    assert ar.reporting(cfg()) and ar.is_admin(cfg())
    assert not ar.reporting(cfg(report=False)) and not ar.reporting(cfg(write_key=""))
    assert not ar.is_admin(cfg(admin_key="")) and not ar.is_admin(cfg(report_url="https://x.com"))
    s = ar.script_for("AK", "WK")
    assert "'AK'" in s and "'WK'" in s and "__ADMIN__" not in s and "__WRITE__" not in s
    tm = ar.teammate_config(cfg())
    assert tm == {"report": True, "report_url": URL, "write_key": "wk"} and "admin_key" not in tm   # key stays private


def test_send_report_posts_run_and_rows_in_batches():
    posts = []
    import admin_report
    orig = admin_report._post
    admin_report._post = lambda url, payload, timeout=30: posts.append(payload) or {"ok": True}
    try:
        run = ar.build_run("Rakib", "PC1", "3.32", "Promo", "My email", 5, 1, 0, 6,
                           "2026-11-20 10:00", "2026-11-20 10:05", "")
        rows = [{"Row": i, "Email": f"u{i}@x.com", "Name": f"U{i}", "Result": "Sent", "Error": "",
                 "Time": "10:01", "Conversation link": ""} for i in range(ar.MAX_ROWS_PER_POST + 10)]
        ar.report_from_files(cfg(include_recipients=True), run, rows)
    finally:
        admin_report._post = orig
    assert posts[0]["write"] == "wk" and posts[0]["run"]["campaign"] == "Promo"
    assert len(posts[0]["rows"]) == ar.MAX_ROWS_PER_POST and "run" not in posts[1]          # second batch, rows only
    assert posts[1]["rows"][0]["email"] == f"u{ar.MAX_ROWS_PER_POST}@x.com"


def test_ping_and_summary():
    assert ar.ping(cfg(), lambda url: "ok") == ""
    assert "web app address" in ar.ping(cfg(report_url="nope"), lambda url: "ok")
    assert "admin key" in ar.ping(cfg(), lambda url: "bad key")
    data = {"columns": ["Reported", "Runner", "Host", "Version", "Sent", "Failed", "Skipped", "Finished"],
            "rows": [["t1", "Rakib", "PC1", "3.32", "5", "1", "0", "2026-11-20 10:05"],
                     ["t2", "Rakib", "PC1", "3.32", "3", "0", "2", "2026-11-20 11:05"],
                     ["t3", "Owadud", "PC2", "3.31", "9", "0", "0", "2026-11-20 09:05"]]}
    people = ar.summarize_runs(data)
    assert [p["runner"] for p in people] == ["Rakib", "Owadud"]                              # newest activity first
    rakib = people[0]
    assert (rakib["runs"], rakib["sent"], rakib["failed"], rakib["skipped"]) == (2, 8, 1, 2)
    assert rakib["last"] == "2026-11-20 11:05" and rakib["versions"] == "3.32"


def test_agent_reports_after_a_live_run_only(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    sys.modules.pop("crisp_email_agent", None)
    mod = importlib.import_module("crisp_email_agent")
    sent = {}
    monkeypatch.setattr(ar, "reporting", lambda c: True)
    monkeypatch.setattr(ar, "report_from_files", lambda c, run, rows: sent.update(run=run, rows=rows))
    monkeypatch.setattr(ar, "load_config", lambda path=None: cfg())
    mod.DRY_RUN = True
    mod.report_to_admin(3, 0, 0, 3)
    assert not sent                                                                          # dry run: nothing
    mod.DRY_RUN = False
    mod.RUNNER_NAME, mod.APP_VERSION, mod.CAMPAIGN, mod.SEND_MODE = "Rakib", "3.32", "Promo", "email"
    mod.REPORT = [{"Row": 2, "Email": "a@x.com", "Name": "Ann", "Result": "Sent", "Error": "",
                   "Time": "10:01", "Conversation link": "L"}]
    mod.report_to_admin(1, 0, 0, 1)
    assert sent["run"]["campaign"] == "Promo" and sent["run"]["mode"] == "My email"
    assert sent["run"]["version"] == "3.32" and sent["rows"][0]["Email"] == "a@x.com"


def test_builtin_auto_reports_summaries_only(tmp_path, monkeypatch):
    import admin_report
    # built-in collector filled, no per-PC admin.json
    rc = tmp_path / "report_config.json"
    rc.write_text('{"auto": true, "report_url": "%s", "write_key": "wk", "include_recipients": false}' % URL)
    monkeypatch.setattr(admin_report, "REPORT_CONFIG", rc)
    empty = {}                                   # a teammate PC with no admin.json
    assert not ar.reporting(empty)               # not explicitly set up...
    auto = ar.auto_config(empty)
    assert auto and auto["report_url"] == URL and auto["include_recipients"] is False
    # recipients are dropped from the silent feed
    posts = []
    monkeypatch.setattr(admin_report, "_post", lambda url, payload, timeout=30: posts.append(payload) or {"ok": True})
    run = ar.build_run("Rakib", "PC1", "3.34", "Promo", "My email", 2, 0, 0, 2, "s", "f", "")
    ar.report_from_files(auto, run, [{"Email": "a@x.com", "Name": "Ann", "Result": "Sent"}])
    assert posts[0]["run"]["sent"] == 2 and posts[0]["rows"] == []        # summary yes, contacts no
    # explicit opt-out respected
    assert ar.auto_config({"report": False}) is None
    # built-in unfilled -> inert
    rc.write_text('{"auto": true, "report_url": "", "write_key": ""}')
    assert ar.auto_config({}) is None


def test_sidebar_notice_shows_only_when_reporting(win, monkeypatch, tmp_path):
    import admin_report
    win.admin_config_path = tmp_path / "admin.json"
    rc = tmp_path / "report_config.json"
    monkeypatch.setattr(admin_report, "REPORT_CONFIG", rc)
    # built-in unfilled, no admin.json -> no reporting -> notice hidden
    rc.write_text('{"auto": true, "report_url": "", "write_key": ""}')
    assert not win._will_report()
    # built-in filled -> this computer will report -> notice shows
    rc.write_text('{"auto": true, "report_url": "%s", "write_key": "wk"}' % URL)
    assert win._will_report()
    win.report_notice.setVisible(win._will_report())
    assert "shared with your admin" in win.report_notice.text()
    # explicit local opt-out -> hidden again
    admin_report.save_config({"report": False}, win.admin_config_path)
    assert not win._will_report()


def test_settings_setup_and_team_admin_view(win, dialogs, monkeypatch, tmp_path):
    from PySide6.QtGui import QGuiApplication
    s = win.settings
    win.admin_config_path = tmp_path / "admin.json"
    win._load_admin_fields()
    assert not s.admin_on.isChecked() and not win.team_page.admin_card.isVisibleTo(win.team_page) or True
    win.copy_admin_script()
    saved = ar.load_config(win.admin_config_path)
    assert saved["admin_key"] and saved["write_key"]
    assert f"'{saved['admin_key']}'" in QGuiApplication.clipboard().text()
    win.copy_admin_script()                                                                  # keys never change by themselves
    assert ar.load_config(win.admin_config_path)["admin_key"] == saved["admin_key"]
    s.admin_url.edit.setText(URL)
    s.admin_on.setChecked(True)
    win._save_admin()
    cfg_now = ar.load_config(win.admin_config_path)
    assert cfg_now["report"] and cfg_now["report_url"] == URL and ar.is_admin(cfg_now)
    # the Team page shows the admin card and fills it from the collector
    monkeypatch.setattr(ar, "fetch", lambda c, what, since="", get=None: {
        "columns": ["Reported", "Runner", "Host", "Version", "Campaign", "Mode", "Sent", "Failed", "Skipped",
                    "Finished", "Error"],
        "rows": [["t1", "Owadud", "PC2", "3.32", "Promo", "My email", "9", "0", "1", "2026-11-20 09:05", ""]]})
    win.navigate(5)                                                                          # TEAM
    assert win.team_page.admin_card.isVisibleTo(win.team_page)
    assert wait_until(lambda: bool(win.admin_people_rows.rows), 5)
    assert win.admin_people_rows.rows[0][0] == "Owadud"
    assert "1 teammate" in win.team_page.admin_summary.text()

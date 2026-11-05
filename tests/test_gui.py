"""End-to-end GUI tests (offscreen). Run: python -m pytest tests -v

The window drives tests/fake_agent.py, which prints the real agent's lines,
so every click below goes through the same code paths as a real run.
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QSettings, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog  # noqa: E402

from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui import widgets as W  # noqa: E402
from crisp_gui.config import CAMPAIGN_DEFAULTS, Preferences  # noqa: E402
from crisp_gui.main_window import MainWindow  # noqa: E402
from crisp_gui.theme import DARK, LIGHT, ThemeManager  # noqa: E402

FAKE = ROOT / "tests" / "fake_agent.py"


@pytest.fixture(scope="session")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyle("Fusion")
    return a


@pytest.fixture
def dialogs():
    """Answer dialogs automatically and record them."""
    record = {"answers": [], "seen": []}

    def hook(title, body, details, confirm):
        record["seen"].append(title)
        return record["answers"].pop(0) if record["answers"] else True

    W.DIALOG_HOOK = hook
    yield record
    W.DIALOG_HOOK = None


@pytest.fixture
def env(tmp_path, monkeypatch):
    for k in ("FAKE_FAIL_EMAIL", "FAKE_LOGIN_WAIT", "FAKE_STEP_DELAY"):
        monkeypatch.delenv(k, raising=False)
    out = tmp_path / "env.json"
    monkeypatch.setenv("FAKE_ENV_OUT", str(out))
    return out


def make_csv(path, n=3, sent=(), ref="Shahriar"):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Full Name", "Contact Email", "Reference", "Mail Sent"])
        for i in range(n):
            w.writerow([f"User {i}", f"u{i}@example.com", ref, "TRUE" if i in sent else ""])
        w.writerow(["Other", "o@example.com", "Someone", ""])
    return path


def read_sent(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [r["Mail Sent"] for r in csv.DictReader(f)]


@pytest.fixture
def win(app, tmp_path, dialogs, env):
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat)
    prefs = Preferences(settings)
    tm = ThemeManager(app, "light")
    csv_path = make_csv(tmp_path / "recipients.csv")
    prefs.set_campaign_value("csv", str(csv_path))
    w = MainWindow(tm, prefs, agent_script=FAKE, profile_dir=tmp_path / "profile", data_dir=tmp_path / "data")
    w.resize(1240, 820)
    w.show()
    QTest.qWaitForWindowExposed(w)
    w._csv = csv_path
    w._tmp = tmp_path
    yield w
    if w.runner.is_running():
        w.runner.force_stop()
        w.runner.proc.waitForFinished(3000)
    w.close()


def wait_until(cond, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        QTest.qWait(20)
    return cond()


def run_to_end(w, timeout=20):
    assert wait_until(lambda: w.runner.is_running() or w.last_state is not None, 5)
    assert wait_until(lambda: not w.runner.is_running(), timeout), "agent did not finish"
    QTest.qWait(50)
    return w.last_state


# ================================================================ navigation
def test_every_nav_item_works(win):
    titles = ["Dashboard", "Campaign Setup", "Activity Logs", "Settings", "History", "Campaign Results", "Campaigns"]
    for i, b in enumerate(win.nav_buttons):
        QTest.mouseClick(b, Qt.LeftButton)
        assert win.stack.currentIndex() == i
        assert win.page_title.text() == titles[i]
        assert b.isChecked()
    for i in range(4):
        QTest.keyClick(win, Qt.Key_1 + i, Qt.ControlModifier)
        assert win.stack.currentIndex() == i


def test_dashboard_links(win):
    win.navigate(0)
    QTest.mouseClick(win.dashboard.open_logs, Qt.LeftButton)
    assert win.stack.currentIndex() == 2
    win.navigate(0)
    QTest.mouseClick(win.dashboard.edit_campaign, Qt.LeftButton)
    assert win.stack.currentIndex() == 1


# ================================================================ campaign
def test_campaign_inputs_persist_and_validate(win, app, tmp_path):
    c = win.campaign
    c.subject.edit.setText("Hello subject")
    c.shortcut.edit.setText("my-shortcut")
    c.segment.edit.setText("my-seg")
    c.reference.edit.setText("Shahriar")
    assert win.dashboard.kv.values["subject"].text() == "Hello subject"
    # persisted: a fresh window loads the same values
    prefs2 = Preferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat))
    assert prefs2.campaign()["shortcut"] == "my-shortcut"
    # required validation
    c.subject.edit.setText("")
    assert c.subject.message.objectName() == "fieldError"
    assert c.subject.edit.property("invalid") is True
    c.subject.edit.setText("ok")
    assert c.subject.edit.property("invalid") is False


def test_csv_stats_and_reference_filter(win):
    win.refresh_csv()
    assert win.dashboard.m_total.value.text() == "4"
    assert win.dashboard.m_eligible.value.text() == "3"
    win.campaign.reference.edit.setText("Someone")
    win.refresh_csv()
    assert win.dashboard.m_eligible.value.text() == "1"
    win.campaign.csv.edit.setText(str(win._tmp / "missing.csv"))
    win.refresh_csv()
    assert win.dashboard.m_total.value.text() == "—"
    assert win.dashboard.csv_banner.isVisibleTo(win.dashboard)
    assert "File not found" in win.campaign.csv.message.text()


def test_browse_csv(win, monkeypatch):
    other = make_csv(win._tmp / "other.csv", n=5)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(other), ""))
    QTest.mouseClick(win.campaign.browse, Qt.LeftButton)
    assert win.campaign.csv.text() == str(other)
    assert win.dashboard.m_eligible.value.text() == "5"
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: ("", ""))
    QTest.mouseClick(win.campaign.browse, Qt.LeftButton)       # cancel keeps value
    assert win.campaign.csv.text() == str(other)


def test_reload_button_rereads_csv(win):
    make_csv(win._csv, n=6)
    QTest.mouseClick(win.campaign.reload, Qt.LeftButton)
    assert win.dashboard.m_eligible.value.text() == "6"


def test_reset_to_defaults(win, dialogs):
    win.campaign.subject.edit.setText("changed")
    dialogs["answers"] = [False]
    QTest.mouseClick(win.campaign.reset, Qt.LeftButton)
    assert win.campaign.subject.text() == "changed"
    dialogs["answers"] = [True]
    QTest.mouseClick(win.settings.reset, Qt.LeftButton)
    assert win.campaign.subject.text() == CAMPAIGN_DEFAULTS["subject"]


def test_dry_run_defaults_on_every_launch(win, app, tmp_path):
    win.campaign.dry.setChecked(False)
    win.close()
    prefs = Preferences(QSettings(str(tmp_path / "prefs.ini"), QSettings.IniFormat))
    w2 = MainWindow(win.tm, prefs, agent_script=FAKE, data_dir=tmp_path / "data")
    assert w2.campaign.dry.isChecked()
    w2.close()


def test_invalid_campaign_blocks_start(win, dialogs):
    win.campaign.shortcut.edit.setText("")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert not win.runner.is_running()
    assert win.stack.currentIndex() == 1
    assert dialogs["seen"] == ["Check the campaign settings"]


def test_no_eligible_blocks_start(win, dialogs):
    make_csv(win._csv, n=2, sent=(0, 1))
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert not win.runner.is_running()
    assert dialogs["seen"] == ["No eligible recipients"]


# ================================================================ dry run
def test_dry_run_on_sends_nothing(win, dialogs, env):
    before = read_sent(win._csv)
    assert win.run_button.text() == "Start dry run"
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    s = run_to_end(win)
    assert dialogs["seen"] == []                                  # no confirmation in dry run
    assert json.loads(env.read_text())["CRISP_DRY_RUN"] == "true"
    assert s.phase == rs.FINISHED and s.dry_run
    assert s.completed == 3 and s.sent_confirmed == 0
    assert read_sent(win._csv) == before                          # CSV untouched
    assert win.dashboard.m_sent.value.text() == "0"
    assert "Dry run" in win.dashboard.m_sent.caption.text()
    assert win.status.text == "Finished"
    assert win.activity.s_done.title.text() == "Passed (dry run)"
    assert win.activity.progress.value() == 1000


def test_real_send_requires_confirmation(win, dialogs, env):
    win.campaign.dry.setChecked(False)
    assert win.run_button.text() == "Start sending"
    assert win.campaign.mode_banner.isVisibleTo(win.campaign)
    dialogs["answers"] = [False]
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert dialogs["seen"] == ["Send 3 real emails?"]
    assert not win.runner.is_running()
    assert not env.exists()                                       # agent never launched


def test_dry_run_off_sends_and_reports_confirmed(win, dialogs, env):
    win.campaign.dry.setChecked(False)
    dialogs["answers"] = [True]
    QTest.mouseClick(win.campaign.start, Qt.LeftButton)
    s = run_to_end(win)
    assert json.loads(env.read_text())["CRISP_DRY_RUN"] == "false"
    assert s.phase == rs.FINISHED and not s.dry_run
    assert s.sent_confirmed == 3
    assert read_sent(win._csv) == ["TRUE", "TRUE", "TRUE", ""]
    assert win.dashboard.m_sent.value.text() == "3"
    assert win.dashboard.m_eligible.value.text() == "0"           # refreshed from CSV
    last = win.prefs.last_run()
    assert last["mode"] == "real" and int(last["sent"]) == 3


def test_failure_is_reported_not_success(win, dialogs, env, monkeypatch):
    monkeypatch.setenv("FAKE_FAIL_EMAIL", "u1@example.com")
    win.campaign.dry.setChecked(False)
    dialogs["answers"] = [True]
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    s = run_to_end(win)
    assert win.runner.proc.exitCode() == 0          # agent exits 0 even on failure…
    assert s.phase == rs.FAILED                     # …but the GUI reports the failure
    assert s.sent_confirmed == 1 and s.failed == 1
    assert win.status.text == "Failed"
    assert win.dashboard.m_failed.value.text() == "1"
    assert win.dashboard.m_sent.value.text() == "1"
    assert win.activity.unconfirmed_banner.isVisibleTo(win.activity)
    assert "u1@example.com" in win.activity.unconfirmed_banner.text.text()
    assert win.activity.error_banner.isVisibleTo(win.activity)
    assert read_sent(win._csv) == ["TRUE", "", "", ""]


def test_inputs_locked_while_running(win, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.1")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.is_running(), 5)
    assert not win.campaign.subject.edit.isEnabled()
    assert not win.campaign.dry.isEnabled()
    assert win.campaign.locked_banner.isVisibleTo(win.campaign)
    assert win.run_button.text() == "Stop"
    run_to_end(win)
    assert win.campaign.subject.edit.isEnabled()
    assert win.run_button.text() == "Start dry run"


def test_live_progress_and_current_recipient(win, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.05")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.state and win.runner.state.position == 2, 10)
    assert "Recipient 2 of 3: u1@example.com" in win.activity.recipient.text()
    assert 300 <= win.activity.progress.value() < 700
    run_to_end(win)


# ================================================================ stop
def test_graceful_stop_finishes_current_recipient(win, dialogs, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.08")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.state and win.runner.state.position == 1, 10)
    QTest.mouseClick(win.activity.stop, Qt.LeftButton)
    assert dialogs["seen"][-1] == "Stop the run?"
    assert win.runner.state.phase == rs.STOPPING
    assert win.run_button.text() == "Force stop"
    s = run_to_end(win)
    assert s.phase == rs.STOPPED
    assert s.completed == 1 and s.position == 1            # finished #1, never started #2
    assert win.status.text == "Stopped"


def test_stop_declined_keeps_running(win, dialogs, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.05")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.state and win.runner.state.position == 1, 10)
    dialogs["answers"] = [False]
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    s = run_to_end(win)
    assert s.phase == rs.FINISHED and s.completed == 3


def test_stop_during_login_kills_immediately(win, dialogs, monkeypatch):
    monkeypatch.setenv("FAKE_LOGIN_WAIT", "60")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.state and win.runner.state.phase == rs.WAITING_LOGIN, 10)
    assert win.status.text == "Waiting for login"
    t = time.time()
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    s = run_to_end(win, timeout=5)
    assert time.time() - t < 5
    assert s.phase == rs.STOPPED and s.completed == 0


def test_force_stop(win, dialogs, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.3")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.state and win.runner.state.position == 1, 10)
    QTest.mouseClick(win.run_button, Qt.LeftButton)             # graceful
    QTest.mouseClick(win.run_button, Qt.LeftButton)             # force
    assert dialogs["seen"][-1] == "Force stop now?"
    s = run_to_end(win, timeout=5)
    assert s.phase == rs.STOPPED


def test_close_while_running(win, dialogs, monkeypatch):
    monkeypatch.setenv("FAKE_LOGIN_WAIT", "60")
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    assert wait_until(lambda: win.runner.is_running(), 5)
    dialogs["answers"] = [False]
    win.close()
    assert win.isVisible() and win.runner.is_running()
    dialogs["answers"] = [True]
    win.close()
    assert not win.runner.is_running()


# ================================================================ log controls
def test_log_clear_copy_save(win, monkeypatch):
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    run_to_end(win)
    text = win.activity.log.toPlainText()
    assert "SUCCESS." in text and "Run started" in text
    QTest.mouseClick(win.activity.copy, Qt.LeftButton)
    assert QGuiApplication.clipboard().text() == text
    target = win._tmp / "saved.log"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    QTest.mouseClick(win.activity.save, Qt.LeftButton)
    assert target.read_text(encoding="utf-8") == text
    QTest.mouseClick(win.activity.clear, Qt.LeftButton)
    assert win.activity.log.toPlainText() == ""


# ================================================================ theme
def test_theme_switching_everywhere(win, app, tmp_path):
    seg = win.settings.theme.buttons
    QTest.mouseClick(seg["dark"], Qt.LeftButton)
    assert win.tm.palette is DARK
    assert DARK.bg.lower() in app.styleSheet().lower()
    assert win.prefs.theme() == "dark"
    # every page and a dialog render with the dark background
    for i in range(4):
        win.navigate(i)
        img = win.stack.currentWidget().grab().toImage()
        assert img.pixelColor(img.width() - 20, img.height() - 5).name() == DARK.bg.lower()
    dlg = W.Dialog(win, "t", "b")
    dlg.show()
    QApplication.processEvents()
    img = dlg.grab().toImage()
    assert img.pixelColor(3, 3).name() == DARK.surface.lower()
    dlg.close()
    QTest.mouseClick(win.theme_toggle, Qt.LeftButton)             # sidebar quick toggle
    assert win.tm.palette is LIGHT
    assert win.settings.theme.value() == "light"
    QTest.mouseClick(seg["system"], Qt.LeftButton)
    assert win.prefs.theme() == "system"
    assert win.tm.palette in (LIGHT, DARK)


# ================================================================ resizing
@pytest.mark.parametrize("size", [(880, 600), (1024, 700), (1366, 768), (1920, 1080)])
def test_resizing_no_horizontal_overflow(win, size):
    win.resize(*size)
    for i in range(4):
        win.navigate(i)
        QApplication.processEvents()
        page = win.stack.currentWidget()
        assert page.horizontalScrollBar().maximum() == 0
        assert page.widget().width() <= page.viewport().width()
    cols = win.dashboard.metrics._cols
    assert cols in (2, 4)
    if size[0] <= 900:
        assert cols == 2
    if size[0] >= 1366:
        assert cols == 4


def test_update_available_shows_button_and_blocks_run_while_installing(win, dialogs):
    from crisp_gui.updater import Release
    s = win.settings
    assert not s.install_update.isVisibleTo(s)
    rel = Release(tag="v9.9", version="9.9", zip_name="x.zip", zip_url="https://x/zip")
    win.updater.release = rel
    win._update_checked(rel, "Version 9.9 is available.")
    assert s.install_update.isVisibleTo(s)
    assert "9.9 available" in win.version_label.text()

    started = []
    win.updater.install = lambda: started.append(True) or True
    win.install_update()                       # dialog hook answers "Update and restart"
    assert started and dialogs["seen"][-1] == "Update to version 9.9?"

    win.updater.busy = True                    # install in progress: no run may start
    win.start_run()
    assert not win.runner.is_running()
    win.updater.busy = False
    win._update_install_failed("Update failed, so version 3.0 was restored.")
    assert dialogs["seen"][-1] == "Update failed"
    assert s.install_update.isEnabled()


def test_row_range_preview_validation_and_reset(win, dialogs, tmp_path):
    c = win.campaign
    total = win.stats.total
    c.row_from.edit.setText("1")
    c.row_to.edit.setText("1")
    win.refresh_csv()
    assert win.stats.range_set and win.stats.in_range == 1
    assert c.csv.message.text().startswith(f"Rows 1–1 of {total}")
    assert win.prefs.campaign()["row_to"] == "1"

    c.row_to.edit.setText(str(total + 5))           # past the end -> error, run blocked
    win.refresh_csv()
    assert "only" in c.row_to.message.text()
    win.start_run()
    assert not win.runner.is_running()
    assert dialogs["seen"][-1] == "Check the campaign settings"

    other = make_csv(tmp_path / "other.csv")        # a different file clears the range
    c.csv.edit.setText(str(other))
    assert c.row_from.text() == "" and c.row_to.text() == ""
    assert win.prefs.campaign()["row_from"] == ""


def test_crash_recovery_warning_and_clear(win, dialogs):
    win.prefs.set_recovery(email="", csv="x.csv", started="2026-11-04 10:00", mode="real sending")
    win.prefs.set_recovery(email="stuck@example.com")
    win._show_crash_recovery()
    banner = win.dashboard.unconfirmed_banner
    assert banner.isVisibleTo(win.dashboard) and "stuck@example.com" in banner.text.text()

    win.start_run()                                   # dry run with the fake agent
    assert win.prefs.recovery() is not None           # marked while running
    assert wait_until(lambda: not win.runner.is_running(), 30)
    QApplication.processEvents()
    assert win.prefs.recovery() is None               # clean finish clears it
    assert win.campaign.skip_failed.isChecked() is False


def test_sending_control_validation_and_run_folder(win, dialogs, tmp_path):
    c = win.campaign
    c.send_window.edit.setText("10-18")
    win.refresh_csv()
    assert "HH:MM" in c.send_window.message.text()
    win.start_run()
    assert not win.runner.is_running() and dialogs["seen"][-1] == "Check the campaign settings"
    c.send_window.edit.setText("")
    c.start_at.edit.setText("07:30")
    win.refresh_csv()
    assert c.start_at.message.text().startswith("Starts ")
    c.start_at.edit.setText("")
    win.refresh_csv()

    win.start_run()                                    # dry run, fake agent
    assert wait_until(lambda: not win.runner.is_running(), 30)
    QApplication.processEvents()
    run = win.run_dir
    assert run and (run / "settings.json").exists() and (run / "run.log").exists()
    assert (run / "summary.json").exists()
    assert "Eligible recipients" in (run / "run.log").read_text(encoding="utf-8")
    win.navigate(4)
    assert win.history.table.rowCount() == 1
    assert win.history.table.item(0, 1).text() == "Dry run"


def test_retry_button_and_diagnostics(win, dialogs, tmp_path):
    path = win._csv
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    rows[0]["Status"] = "Failed"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    win.refresh_csv()
    assert win.campaign.retry_failed.isEnabled()
    assert win.campaign.retry_failed.text() == "Retry failed only (1)"

    win.navigate(4)
    QTest.mouseClick(win.history.copy_diag, Qt.LeftButton)
    zips = list((tmp_path / "data" / "diagnostics").glob("diagnostics-*.zip"))
    assert len(zips) == 1 and str(zips[0]) == QGuiApplication.clipboard().text()


def test_event_lines_hidden_and_health_result_shown(win):
    win._agent_line('@@EVENT {"event": "eligible", "count": 1}')
    win._agent_line("visible line")
    text = win.activity.log.toPlainText()
    assert "@@EVENT" not in text and "visible line" in text
    st = rs.RunState()
    st.health = [("Subject dropdown", True, "found"), ("Segment field", False, "not found")]
    win._health = True
    win._run_finished(st)
    assert win.settings.health_result.text() == "✓ Subject dropdown: found\n✗ Segment field: not found"
    assert win.prefs.last_run() is None          # a health check is not a run


def test_google_sheet_source_test_connection(win, tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sheet_source as ss
    from fake_sheets import FakeClient, FakeSpreadsheet
    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    book = FakeSpreadsheet({"Customers": [["Full Name", "Contact Email", "Reference", "Mail Sent"],
                                          ["A", "a@x.com", "Shahriar", ""], ["B", "a@x.com", "Shahriar", ""],
                                          ["C", "c@x.com", "Shahriar", "TRUE"]]})
    win._sheet_factory = lambda url, tab, key: ss.SheetSource(url, tab, key, client=FakeClient(book, sid)).connect()
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "service_account", "client_email": "bot@p.iam.gserviceaccount.com"}))
    c = win.campaign
    win._source_changed("sheet")
    assert c.sheet_box.isVisibleTo(c) and not c.csv_box.isVisibleTo(c)
    c.sheet_url.edit.setText(f"https://docs.google.com/spreadsheets/d/{sid}/edit")
    c.google_key.edit.setText(str(key))
    win.test_sheet()                                   # no tab chosen: picks the first tab itself
    assert wait_until(lambda: not win._sheet_busy, 10)
    QApplication.processEvents()
    assert [c.sheet_tab.itemText(i) for i in range(c.sheet_tab.count())] == ["Customers"]
    assert (win.stats.total, win.stats.eligible, win.stats.duplicates) == (3, 1, 1)
    assert "Connected as bot@p.iam.gserviceaccount.com" in c.sheet_info.text()
    v = win.campaign_values()
    assert (v["source"], v["sheet_tab"]) == ("sheet", "Customers")
    assert win.prefs.source() == "sheet"
    win._source_changed("csv")


def test_sheet_calls_never_freeze_the_window(win, tmp_path):
    import threading as th
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sheet_source as ss
    gate = th.Event()

    def slow_factory(url, tab, key):
        gate.wait(5)                                   # Google is slow...
        raise ss.SheetError("The Google Sheets API is turned off in your Google Cloud project.")
    win._sheet_factory = slow_factory
    key = tmp_path / "key.json"
    key.write_text(json.dumps({"type": "service_account", "client_email": "bot@p.iam.gserviceaccount.com"}))
    c = win.campaign
    win._source_changed("sheet")
    c.sheet_url.edit.setText("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit")
    c.google_key.edit.setText(str(key))
    t = time.time()
    win.load_sheet_tabs()
    assert time.time() - t < 0.5                       # ...but the click returns at once
    assert c.test_sheet.text() == "Connecting…" and not c.load_tabs.isEnabled()
    QApplication.processEvents()                        # window keeps painting/responding
    gate.set()
    assert wait_until(lambda: not win._sheet_busy, 10)
    QApplication.processEvents()
    assert "API is turned off" in c.sheet_info.text() and c.load_tabs.isEnabled()
    win._source_changed("csv")


def test_preview_rows_and_live_confirmation_table(win, dialogs, monkeypatch):
    seen = {}

    def hook(title, body, details, confirm):
        seen[title] = (body, details)
        dialogs["seen"].append(title)
        return False                                   # cancel the live run
    monkeypatch.setattr(W, "DIALOG_HOOK", hook)
    win.show_row_preview()
    body, rows = seen["Preview: what a run would do"]
    assert body.startswith("Found ") and rows and rows[0][-1] == "Send"
    assert rows[0][3] == win.campaign.subject.text()   # subject column filled
    win.campaign.dry.setChecked(False)
    win.start_run()
    title = [t for t in seen if t.startswith("Send ")][0]
    assert "This can't be undone" in seen[title][0] and len(seen[title][1]) == len(rows)
    assert not win.runner.is_running()                 # cancelled in the table


def test_pause_button_writes_and_clears_pause_file(win, dialogs, env, monkeypatch):
    monkeypatch.setenv("FAKE_STEP_DELAY", "0.3")
    win.start_run()
    assert wait_until(lambda: win.runner.is_running(), 10)
    assert wait_until(lambda: win.activity.pause.isEnabled(), 15)
    QTest.mouseClick(win.activity.pause, Qt.LeftButton)
    assert win.runner.pause_requested() and win.activity.pause.text() == "Resume"
    QTest.mouseClick(win.activity.pause, Qt.LeftButton)
    assert not win.runner.pause_requested() and win.activity.pause.text() == "Pause"
    win.runner.force_stop()
    assert wait_until(lambda: not win.runner.is_running(), 15)


def test_campaign_results_page(win, dialogs, tmp_path, monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import campaign_results as cr
    import send_control as sc
    from datetime import datetime
    hist = tmp_path / "data" / "history.csv"
    for email, version, link in (("a@x.com", "v1", "https://app.crisp.chat/website/w/inbox/session_a/"),
                                 ("b@x.com", "v2", "https://app.crisp.chat/website/w/inbox/session_b/"),
                                 ("old@x.com", "", "")):
        sc.append_history(hist, Email=email, Result="Sent", Mode="live", Campaign="VIP", Version=version, Link=link)
    win.navigate(5)
    r = win.results_page
    assert [r.campaign.itemText(i) for i in range(r.campaign.count())] == ["All campaigns", "VIP"]
    assert "3 sent" in r.summary.text() and "1 without a conversation link" in r.last_checked.text()
    now = datetime.now()
    win.results.apply(cr.rkey("VIP", "a@x.com"), {"status": "Replied", "reply_at": "", "open": True, "problem": ""}, now)
    win.results.apply(cr.rkey("VIP", "b@x.com"), {"status": "Failed", "reason": "bounced", "problem": ""}, now)
    win.refresh_results()
    assert r.m_replied.value.text() == "33.3%" and r.m_failed.value.text() == "1"
    assert r.followup.rowCount() == 1 and r.problems.rowCount() == 1
    assert "Read is approximate" in r.read_note.text()
    r.f_status.setCurrentText("Failed")
    assert r.table.rowCount() == 1
    r.f_status.setCurrentText("All statuses")
    out = tmp_path / "export.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    win.export_results()
    assert out.exists() and "b@x.com" in out.read_text(encoding="utf-8-sig")
    dnc = tmp_path / "dnc.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(dnc), ""))
    dialogs["answers"] = [False]
    win.add_failed_to_dnc()                            # user says no -> nothing written
    assert not dnc.exists()
    win.add_failed_to_dnc()                            # user says yes
    assert dnc.read_text(encoding="utf-8").splitlines() == ["Email", "b@x.com"]
    assert win.campaign.dnc.text() == str(dnc)

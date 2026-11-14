"""Phase 3 UI smoke tests: the main flow (choose campaign -> check -> Dry Run -> Send),
Live vs Dry Run always unmistakable, progressive disclosure, Lists and Team pages."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import claims as cl  # noqa: E402
import sheet_source as ss  # noqa: E402
from crisp_gui import campaign_store as cs  # noqa: E402
from crisp_gui import run_state as rs  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402
from test_gui import app, dialogs, env, make_csv, run_to_end, wait_until, win  # noqa: E402,F401

EDITOR, RUN, LISTS = 1, 2, 4


def test_main_flow_choose_check_dry_run_then_send(win, dialogs, monkeypatch):
    seen = []
    from crisp_gui import widgets as W

    def hook(title, body, rows, confirm):
        seen.append((title, body, rows, confirm))
        dialogs["seen"].append(title)
        return title.startswith("Dry") or "Stop" in title or not title.startswith("Send")
    monkeypatch.setattr(W, "DIALOG_HOOK", hook)

    # start: Campaigns, empty state, nothing to run
    win.navigate(0)
    cp = win.campaigns_page
    assert cp.empty.isVisibleTo(cp) and not cp.run_queue.isEnabled()
    assert not win.run_button.isVisible()                              # one primary action: Run queue

    # choose: new campaign, fill it in, save
    QTest.keyClick(win, Qt.Key_N, Qt.ControlModifier)
    assert win.stack.currentIndex() == EDITOR and win.page_title.text() == "New campaign"
    c = win.campaign
    c.campaign_name.edit.setText("Spring")
    c.csv.edit.setText(str(win._csv))
    c.subject.edit.setText("Hello")
    c.shortcut.edit.setText("!a")
    c.segment.edit.setText("seg")
    c.reference.edit.setText("Shahriar")
    QTest.mouseClick(c.save, Qt.LeftButton)
    saved = win.campaign_store.by_name("Spring")
    assert saved is not None and win.page_title.text() == "Campaign · Spring"

    # check: the numbers and the per-row list
    win.refresh_csv()
    assert c.m_eligible.value.text() == "3" and c.m_total.value.text() == "4"
    win.navigate(LISTS)
    assert "3 to send" in win.lists_page.summary.text() and win.list_rows.rowCount() == 4
    win.lists_page.filter.set_value("skip")
    win.refresh_lists()
    assert win.list_rows.rowCount() == 1 and win.list_rows.rows[0][-1].startswith("Skip")

    # Dry Run is unmistakable and is the default
    assert c.dry.isChecked() and win.mode_label.text() == "Dry Run"
    assert win.run_button.text() == "Start dry run" and win.run_button.isVisible()
    win.navigate(EDITOR)
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    s = run_to_end(win, 30)
    assert s.phase == rs.FINISHED and win.stack.currentIndex() == RUN
    assert win.run_rows.rowCount() == 3 and win.run_rows.rows[0][2] == "Dry run passed"

    # Live: the switch says LIVE, the button is the danger style, the confirmation is exact
    win.navigate(EDITOR)
    c.dry.setChecked(False)
    assert win.mode_label.text() == "LIVE" and win.run_button.text() == "Start sending"
    assert win.run_button.objectName() == "danger"
    assert c.mode_banner.isVisibleTo(c) and "3 recipients" in c.mode_banner.text.text()
    QTest.mouseClick(win.run_button, Qt.LeftButton)
    title, body, rows, confirm = seen[-1]
    assert title == "Send 3 real emails?" and confirm == "Send 3 emails"
    assert "Campaign: Spring" in body and "Subject: Hello" in body and "Shortcut: !a" in body
    assert not win.runner.is_running()                                  # cancelled: nothing sent


def test_run_button_belongs_to_editor_run_and_lists(win):
    for page, visible in ((0, False), (1, True), (2, True), (3, False), (4, True), (5, False), (6, False)):
        win.navigate(page)
        QApplication.processEvents()
        assert win.run_button.isVisible() is visible, page


def test_advanced_is_collapsed_and_opens_with_the_keyboard(win):
    c, st = win.campaign, win.settings
    assert not c.advanced.is_open()
    assert not c.delay.isVisibleTo(c) and c.versions.isVisibleTo(c)      # common visible, rest folded
    # Settings: grouped rows all visible; only the long Crisp API and AI forms fold out
    assert st.runner_name.isVisibleTo(st) and st.claim_batch.isVisibleTo(st)
    assert not st.api_key.isVisibleTo(st) and not st.ai_key.isVisibleTo(st)
    st.ai_row.toggle.click()
    assert st.ai_key.isVisibleTo(st) and st.ai_row.toggle.text() == "Done"
    c.advanced.head.setFocus()
    QTest.keyClick(c.advanced.head, Qt.Key_Space)
    assert c.advanced.is_open() and c.delay.isVisibleTo(c)


def test_campaign_actions_follow_the_selected_status(win):
    store = win.campaign_store
    a = store.add("A", {"subject": "s", "shortcut": "x", "segment": "g", "csv": str(win._csv)})
    store.enqueue(a.id)
    win.navigate(0)
    win.refresh_campaigns(select=a.id)
    cp = win.campaigns_page
    assert cp.dequeue.isVisibleTo(cp) and not cp.enqueue.isVisibleTo(cp) and not cp.stop.isVisibleTo(cp)
    assert cp.run_queue.isEnabled() and cp.run_queue.text() == "Run queue (dry run)"
    win.campaign.dry.setChecked(False)
    assert cp.run_queue.text() == "Run queue: LIVE" and cp.run_queue.objectName() == "danger"
    store.set_status(a.id, cs.UNCONFIRMED, "check")
    win.refresh_campaigns(select=a.id)
    assert cp.checked.isVisibleTo(cp) and not cp.enqueue.isVisibleTo(cp)
    assert cp.banner.isVisibleTo(cp) and "I checked Crisp" in cp.banner.text.text()


def test_team_page_shows_runners_and_records_a_check(win, dialogs, monkeypatch):
    monkeypatch.setattr(ss.time, "sleep", lambda s: None)               # no real pacing waits in tests
    win.navigate(5)
    assert "Google Sheet campaigns" in win.team_page.info.text.text()

    sid = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
    book = FakeSpreadsheet({"Customers": [["Contact Email", "Mail Sent"], ["a@x.com", "FALSE"]]})
    factory = lambda url, tab, key: ss.SheetSource(url, tab, key, client=FakeClient(book, sid)).connect()  # noqa: E731
    monkeypatch.setattr(win, "_sheet_factory", factory)
    other = cl.Claims(factory(f"https://docs.google.com/spreadsheets/d/{sid}/", "Customers", "k"), "VIP", runner="Ana (PC-2)", batch=1)
    other.start(heartbeat=False)
    other.claim_batch(["a@x.com"])
    other.begin_send("a@x.com")                                         # Ana's PC died after clicking Send
    c = win.campaign
    win._apply_campaign_values({"source": "sheet", "sheet_url": f"https://docs.google.com/spreadsheets/d/{sid}/",
                                "sheet_tab": "Customers", "google_key": "k.json"})
    win.navigate(5)
    assert wait_until(lambda: win.team_rows.rowCount() == 1, 10), (win.team_page.status.text(), win.team_page.info.text.text(), win._team_source())
    assert win.team_rows.rows[0][0] == "Ana (PC-2)" and win.check_rows.rows[0][:2] == ("a@x.com", "Sending")
    win.team_page.checks.selectRow(0)
    win.resolve_check(False)                                            # a person checked: not sent
    assert wait_until(lambda: win.check_rows.rowCount() == 0, 10), (dialogs["seen"], win.team_page.status.text(), book.tabs[cl.CLAIMS_TAB].values[-2:])
    snap = cl.read_snapshot(factory(f"https://docs.google.com/spreadsheets/d/{sid}/", "Customers", "k"))
    assert snap.claims["a@x.com"][0].state == cl.RELEASED and "manual" in snap.claims["a@x.com"][0].note
    win.navigate(0)
    assert not win.team_timer.isActive()                               # refresh only while visible
    c.source.set_value("csv")


def test_version_table_edits_the_saved_subject_shortcut_and_labels(win):
    c = win.campaign
    vt = c.versions
    c.shortcut.edit.setText("!a")
    c.subject.edit.setText("Free plugin, Shahriar")
    c.shortcut_version.edit.setText("")
    assert vt.rows() == [("!a", "Free plugin, Shahriar", "")]          # fields -> table
    vt.add_version()                                                    # same subject, new row
    vt.table.item(1, vt.SUBJECT).setText("Your offer")                  # A/B on the subject only
    vt.table.item(1, vt.SHORTCUT).setText("!a")
    assert (c.shortcut.text(), c.subject.text(), c.shortcut_version.text()) == \
        ("!a", "Free plugin, Shahriar | Your offer", "v1, v2")
    win.refresh_csv()
    assert vt.status.objectName() == "fieldOk" and "2 versions" in vt.status.text()
    vt.table.item(1, vt.SUBJECT).setText("Free plugin, Shahriar")       # same subject twice + same shortcut
    win.refresh_csv()
    assert vt.status.objectName() == "fieldError"
    vt.table.selectRow(1)
    vt.remove_selected()
    assert vt.table.rowCount() == 1 and c.subject.text() == "Free plugin, Shahriar"
    win._send_mode_changed("api")
    assert vt.table.isColumnHidden(vt.SHORTCUT)


def test_emailed_recently_switch_saves_days_or_zero(win):
    c = win.campaign
    rule = c.recent_rule
    c.recent_days.edit.setText("5")
    assert rule.mode.value() == "skip" and rule.days_row.isVisibleTo(rule)
    rule.mode.buttons["allow"].click()
    assert c.recent_days.text() == "0" and not rule.days_row.isVisibleTo(rule)
    assert win.campaign_values()["recent_days"] == "0"
    rule.mode.buttons["skip"].click()
    assert c.recent_days.text() == "5"                                   # the old number comes back
    c.recent_days.edit.setText("0")                                      # a saved campaign with 0 loads as allow
    assert rule.mode.value() == "allow"

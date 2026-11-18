"""Settings tabs, account status pills, the 'Set up' link next to Send with, and the
Campaigns table's Send with column."""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mail_sender as ms  # noqa: E402


@pytest.fixture
def win(tmp_path, monkeypatch):
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CRISP_MAIL_CONFIG", str(tmp_path / "mail.json"))
    monkeypatch.setenv("CRISP_API_CONFIG", str(tmp_path / "api.json"))
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    prefs.set_campaign_value("csv", str(tg.make_csv(tmp_path / "list.csv", n=2)))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    yield w
    w.close()


def visible_groups(s):
    return [tab for tab, groups in s._tab_groups.items() if all(not g.isHidden() for g in groups)]


def test_settings_opens_on_accounts_and_tabs_switch(win):
    s = win.settings
    assert s.tabs.value() == "accounts" and visible_groups(s) == ["accounts"]
    s.show_tab("app")
    assert visible_groups(s) == ["app"] and s.tabs.value() == "app"


def test_account_rows_show_status_without_opening(win, tmp_path):
    from crisp_gui.main_window import SETTINGS
    s = win.settings
    win.navigate(SETTINGS)
    assert s.mail_row.pill.text() == "Not set up" and s.mail_row.toggle.text() == "Set up"
    assert s.api_row.pill.text() == "Not set up"
    ms.save_config({"email": "me@example.com", "password": "p", "host": "smtp.example.com"}, tmp_path / "mail.json")
    win.navigate(SETTINGS)
    assert s.mail_row.pill.text() == "me@example.com" and s.mail_row.pill.objectName() == "pillOk"
    assert s.mail_row.toggle.text() == "Edit"


def test_set_up_link_opens_the_email_account_form(win):
    from crisp_gui.main_window import SETTINGS
    c = win.campaign
    win._send_mode_changed("email")
    c.api_message.setPlainText("Hi {first_name}")
    win._validate_fields()
    assert not c.send_setup.isHidden() and "My email" in c.send_setup_hint.text()
    c.send_setup.click()
    assert win.stack.currentIndex() == SETTINGS
    assert win.settings.tabs.value() == "accounts" and win.settings.mail_row.toggle.isChecked()


def test_campaigns_table_says_how_each_campaign_sends(win):
    from crisp_gui.main_window import MainWindow
    assert MainWindow._send_with_text({"send_mode": "email"}) == "My email"
    assert MainWindow._send_with_text({"send_mode": "api"}) == "Crisp API"
    assert MainWindow._send_with_text({"shortcut": "!bf"}) == "Browser · !bf"
    assert "Send with" in win.campaigns_page.COLUMNS

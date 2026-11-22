"""The app's own _crisp_ tabs (claims log, runners, control) can never be a recipient list."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sheet_source as ss  # noqa: E402
from fake_sheets import FakeClient, FakeSpreadsheet  # noqa: E402

SID = "1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
URL = f"https://docs.google.com/spreadsheets/d/{SID}/edit#gid=0"


def book():
    return FakeSpreadsheet({"Customers": [["Email"], ["a@x.com"]],
                            "_crisp_claims": [["claim", "email"], ["#schema", "x"]],
                            "_crisp_runners": [["runner"]], "_crisp_control": [["Running"]]})


def test_tab_list_leaves_out_the_apps_own_tabs():
    src = ss.SheetSource(URL, "Customers", "k.json", client=FakeClient(book(), SID)).connect()
    assert src.tabs() == ["Customers"]


def test_an_internal_tab_is_refused_with_a_clear_message():
    src = ss.SheetSource(URL, "_crisp_claims", "k.json", client=FakeClient(book(), SID)).connect()
    with pytest.raises(ss.SheetError, match="app's own log tab, not a recipient list"):
        src.download()
    assert ss.is_internal_tab(" _CRISP_runners ") and not ss.is_internal_tab("crisp customers")


def test_editor_marks_an_internal_tab_and_blocks_the_run(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import test_gui as tg
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from crisp_gui.config import Preferences
    from crisp_gui.main_window import MainWindow
    from crisp_gui.theme import ThemeManager
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv("CRISP_MAIL_CONFIG", str(tmp_path / "mail.json"))
    prefs = Preferences(QSettings(str(tmp_path / "p.ini"), QSettings.IniFormat))
    w = MainWindow(ThemeManager(app, "light"), prefs, agent_script=tg.FAKE, data_dir=tmp_path / "data")
    try:
        w._apply_source("sheet")
        monkeypatch.setattr(w, "source", lambda: "sheet")
        w.campaign.sheet_tab.addItem("_crisp_claims")
        w.campaign.sheet_tab.setCurrentText("_crisp_claims")
        assert w._validate_fields() is False
        assert "not a recipient list" in w.campaign.sheet_url.message.text()
    finally:
        w.close()

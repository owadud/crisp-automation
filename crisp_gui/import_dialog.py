"""Choose a list (any CSV or any Google Sheet tab) whose recorded sends go into
Campaign Results, and whether to keep it in sync."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QVBoxLayout, QWidget)

from .widgets import Field, SegmentedControl, button, label

# Tests answer the dialog through this: hook(prefill) -> values dict or None.
IMPORT_HOOK = None


class _Tabs(QObject):
    done = Signal(object, str)


class ImportDialog(QDialog):
    def __init__(self, parent, tm, prefill: dict, campaigns: list[str], sheet_factory):
        super().__init__(parent)
        self.setWindowTitle("Import or sync sends")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.sheet_factory = sheet_factory
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(label("Import or sync sends", "dialogTitle"))
        lay.addWidget(label("Choose a list the app sent from (here, on another computer or with an older "
                            "version). Rows with Mail Sent ticked are added with their time and conversation "
                            "link. The list is only read, never changed.", "dialogBody", wrap=True))

        row = QHBoxLayout()
        row.addWidget(label("List", "fieldLabel"))
        self.kind = SegmentedControl([("sheet", "Google Sheet", None), ("csv", "CSV file", None)], tm)
        row.addWidget(self.kind)
        row.addStretch()
        lay.addLayout(row)

        self.csv = Field("CSV file", placeholder="Choose a CSV file")
        self.csv_browse = button("Browse…")
        self.csv_browse.clicked.connect(self._browse_csv)
        self.csv_row = self._with_button(self.csv, self.csv_browse)
        lay.addWidget(self.csv_row)

        self.sheet_url = Field("Google Sheet link", placeholder="https://docs.google.com/spreadsheets/d/…")
        lay.addWidget(self.sheet_url)
        tab_row = QHBoxLayout()
        tab_row.setContentsMargins(0, 0, 0, 0)
        tab_row.addWidget(label("Tab", "fieldLabel"))
        self.tab = QComboBox()
        self.tab.setEditable(True)
        self.tab.setMinimumWidth(200)
        tab_row.addWidget(self.tab, 1)
        self.load_tabs = button("Load tabs")
        self.load_tabs.clicked.connect(self._load_tabs)
        tab_row.addWidget(self.load_tabs)
        self.tab_box = QWidget()
        self.tab_box.setLayout(tab_row)
        lay.addWidget(self.tab_box)
        self.key = Field("Service-account key file", placeholder="The same .json key the app uses")
        self.key_browse = button("Browse…")
        self.key_browse.clicked.connect(self._browse_key)
        self.key_row = self._with_button(self.key, self.key_browse)
        lay.addWidget(self.key_row)

        crow = QHBoxLayout()
        crow.addWidget(label("Campaign", "fieldLabel"))
        self.campaign = QComboBox()
        self.campaign.setEditable(True)
        self.campaign.addItems(campaigns)
        self.campaign.setMinimumWidth(240)
        crow.addWidget(self.campaign, 1)
        lay.addLayout(crow)
        self.reference = Field("Only rows with this Reference", "Blank = every sent row in the list.",
                               placeholder="e.g. owadud")
        lay.addWidget(self.reference)
        self.keep = QCheckBox("Keep in sync: read this list again before every Refresh statuses")
        self.keep.setChecked(True)
        lay.addWidget(self.keep)
        self.error = label("", "fieldError", wrap=True)
        lay.addWidget(self.error)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = button("Import", "primary")
        self.ok.clicked.connect(self._accept)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        lay.addLayout(btns)

        self._tabs_signal = _Tabs(self)
        self._tabs_signal.done.connect(self._tabs_loaded)
        self.kind.changed.connect(self._kind_changed)
        self._fill(prefill)

    @staticmethod
    def _with_button(field, btn):
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(field, 1)
        h.addWidget(btn, 0, Qt.AlignBottom)
        return box

    def _fill(self, p: dict):
        self.kind.set_value(p.get("kind") or "sheet")
        self.csv.edit.setText(p.get("csv", ""))
        self.sheet_url.edit.setText(p.get("sheet_url", ""))
        if p.get("tab"):
            self.tab.addItem(p["tab"])
            self.tab.setCurrentText(p["tab"])
        self.key.edit.setText(p.get("key", ""))
        if p.get("campaign"):
            self.campaign.setCurrentText(p["campaign"])
        self.reference.edit.setText(p.get("reference", ""))
        self._kind_changed(self.kind.value())

    def _kind_changed(self, kind):
        sheet = kind == "sheet"
        for w in (self.sheet_url, self.tab_box, self.key_row):
            w.setVisible(sheet)
        self.csv_row.setVisible(not sheet)

    def _browse_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the list", str(Path.home()),
                                              "CSV files (*.csv);;All files (*)")
        if path:
            self.csv.edit.setText(path)

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the key file", str(Path.home()), "JSON (*.json)")
        if path:
            self.key.edit.setText(path)

    def _load_tabs(self):
        url, key = self.sheet_url.text(), self.key.text()
        self.load_tabs.setEnabled(False)
        self.load_tabs.setText("Loading…")

        def work():
            try:
                self._tabs_signal.done.emit(self.sheet_factory(url, "", key).tabs(), "")
            except Exception as exc:  # noqa: BLE001
                self._tabs_signal.done.emit(None, str(exc))
        threading.Thread(target=work, daemon=True).start()

    def _tabs_loaded(self, tabs, error):
        self.load_tabs.setEnabled(True)
        self.load_tabs.setText("Load tabs")
        if error:
            self.error.setText(error)
            return
        current = self.tab.currentText()
        self.tab.clear()
        self.tab.addItems(tabs)
        if current in tabs:
            self.tab.setCurrentText(current)
        self.error.setText("")

    def values(self) -> dict:
        return {"kind": self.kind.value() or "sheet", "csv": self.csv.text(), "sheet_url": self.sheet_url.text(),
                "tab": self.tab.currentText().strip(), "key": self.key.text(),
                "campaign": self.campaign.currentText().strip(), "reference": self.reference.text(),
                "keep": self.keep.isChecked()}

    def _accept(self):
        problem = check(self.values())
        if problem:
            self.error.setText(problem)
            return
        self.accept()


def check(v: dict) -> str:
    if not v.get("campaign"):
        return "Choose or type the campaign these sends belong to."
    if v.get("kind") == "sheet":
        if "/spreadsheets/d/" not in v.get("sheet_url", ""):
            return "Paste the Google Sheet link (it contains /spreadsheets/d/…)."
        if not v.get("tab"):
            return "Choose the tab (click Load tabs)."
        if not v.get("key"):
            return "Choose the service-account key file."
    elif not v.get("csv") or not Path(v["csv"]).expanduser().is_file():
        return "Choose an existing CSV file."
    return ""


def ask_source(parent, tm, prefill: dict, campaigns: list[str], sheet_factory) -> dict | None:
    if IMPORT_HOOK is not None:
        v = IMPORT_HOOK(dict(prefill))
        return v if v and not check(v) else None
    dlg = ImportDialog(parent, tm, prefill, campaigns, sheet_factory)
    return dlg.values() if dlg.exec() == QDialog.Accepted else None

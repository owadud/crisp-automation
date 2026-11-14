"""The pages. They only build widgets and expose them;
MainWindow wires behaviour so all logic lives in one place."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QPlainTextEdit, QProgressBar, QScrollArea,
    QCheckBox, QLineEdit, QSizePolicy, QTabWidget, QTableView, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

import send_control as sc

from .config import APP_VERSION
from .message_editor import MessageEditor
from .theme import ThemeManager
from .widgets import (
    Banner, Card, Collapsible, Field, KeyValueList, MetricCard, ResponsiveRow,
    SegmentedControl, SettingRow, SettingsGroup, ToggleSwitch, button, label,
)


class Page(QScrollArea):
    """Scrollable page with a centred, width-capped column."""

    MAX_WIDTH = 1080

    def __init__(self):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("page")
        outer = QHBoxLayout(host)
        outer.setContentsMargins(28, 24, 28, 28)
        self.column = QWidget()
        self.column.setObjectName("page")
        self.column.setMaximumWidth(self.MAX_WIDTH)
        self.layout_ = QVBoxLayout(self.column)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(16)
        outer.addWidget(self.column, 1)
        self.setWidget(host)

    def add(self, widget, stretch=0):
        self.layout_.addWidget(widget, stretch)
        return widget

    def finish(self):
        self.layout_.addStretch(1)


class MiniStat(QWidget):
    def __init__(self, title: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.value = label("0", "metricValue")
        self.value.setStyleSheet("font-size: 20px;")
        self.title = label(title, "metricLabel")
        lay.addWidget(self.value)
        lay.addWidget(self.title)


# =====================================================================
class VersionTable(QWidget):
    """A/B versions as rows (label, Crisp shortcut, subject) instead of comma / | text.

    The campaign still saves the three texts it always did (subject, shortcut, version
    labels): this table only reads them and writes them back, so saved campaigns and
    sending are unchanged. Messages from the hidden fields (Required, A/B summary,
    errors) are shown under the table by mirror()."""

    LABEL, SHORTCUT, SUBJECT = range(3)

    def __init__(self, shortcut: "Field", subject: "Field", labels: "Field", tm: ThemeManager):
        super().__init__()
        self.fields = (shortcut, subject, labels)
        self.api = False
        self._syncing = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(label("Versions", "fieldLabel"))
        self.hint = label("", "fieldHelp", wrap=True)
        lay.addWidget(self.hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Label", "Crisp shortcut", "Subject"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        head = self.table.horizontalHeader()
        head.setSectionResizeMode(self.LABEL, QHeaderView.Interactive)
        head.setSectionResizeMode(self.SHORTCUT, QHeaderView.Interactive)
        head.setSectionResizeMode(self.SUBJECT, QHeaderView.Stretch)
        self.table.setColumnWidth(self.LABEL, 90)
        self.table.setColumnWidth(self.SHORTCUT, 200)
        self.table.itemChanged.connect(lambda _item: self._write())
        lay.addWidget(self.table)
        row = QHBoxLayout()
        self.add_row = button("+ Add version")
        self.remove_row = button("Remove selected", "ghost")
        self.add_row.clicked.connect(self.add_version)
        self.remove_row.clicked.connect(self.remove_selected)
        row.addWidget(self.add_row)
        row.addWidget(self.remove_row)
        row.addStretch()
        lay.addLayout(row)
        self.status = label("", "fieldHelp", wrap=True)
        self.status.hide()
        lay.addWidget(self.status)
        for f in self.fields:
            f.edit.textChanged.connect(self._fields_changed)
        self.set_api(False)
        self.load()

    # -------------------------------------------------------------- fields -> table
    def _fields_changed(self, _text=""):
        if not self._syncing:
            self.load()

    def load(self):
        shortcut, subject, labels = (f.text() for f in self.fields)
        rows = sc.versions_from_text(shortcut, subject, labels)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for r in rows:
            self._append(r)
        self.table.blockSignals(False)
        self._fit()

    def _append(self, r):
        i = self.table.rowCount()
        self.table.insertRow(i)
        shortcut, subject, lab = r
        for col, text in ((self.LABEL, lab), (self.SHORTCUT, shortcut), (self.SUBJECT, subject)):
            self.table.setItem(i, col, QTableWidgetItem(text))

    def _fit(self):
        body = self.table.verticalHeader().length() or 32
        self.table.setFixedHeight(self.table.horizontalHeader().sizeHint().height() + body
                                  + 2 * self.table.frameWidth())
        self.remove_row.setEnabled(self.table.rowCount() > 1)

    # -------------------------------------------------------------- table -> fields
    def rows(self):
        def cell(i, c):
            item = self.table.item(i, c)
            return item.text().strip() if item else ""
        return [(cell(i, self.SHORTCUT), cell(i, self.SUBJECT), cell(i, self.LABEL))
                for i in range(self.table.rowCount())]

    def _write(self):
        shortcut, subject, labels = sc.versions_to_text(self.rows())
        self._syncing = True
        try:
            if not self.api:              # API mode: the message texts are the versions, not shortcuts
                self.fields[0].edit.setText(shortcut)
            self.fields[1].edit.setText(subject)
            self.fields[2].edit.setText(labels)
        finally:
            self._syncing = False

    def add_version(self):
        rows = self.rows()
        last = rows[-1] if rows else ("", "", "")
        self.table.blockSignals(True)
        if len(rows) == 1 and not rows[0][2]:
            self.table.item(0, self.LABEL).setText("v1")
        self._append(("", last[1], f"v{len(rows) + 1}"))   # same subject by default: change what differs
        self.table.blockSignals(False)
        self._fit()
        self._write()
        col = self.SUBJECT if self.api else self.SHORTCUT
        self.table.setCurrentCell(self.table.rowCount() - 1, col)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, col))

    def remove_selected(self):
        picked = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not picked and self.table.currentRow() >= 0:
            picked = [self.table.currentRow()]
        for i in picked:
            if self.table.rowCount() > 1:
                self.table.removeRow(i)
        self._fit()
        self._write()

    # -------------------------------------------------------------- mode and messages
    def set_api(self, api: bool):
        self.api = api
        self.table.setColumnHidden(self.SHORTCUT, api)
        self.hint.setText("One row per subject. The message versions are the texts below (--- between them)."
                          if api else "One row per version. Recipients are split evenly between the rows. The "
                          "subject must exactly match a saved subject in Crisp's subject dropdown.")

    def mirror(self):
        """Show the hidden fields' check result under the table: an error first, else the A/B summary."""
        names = {"fieldError": "error", "fieldOk": "ok"}
        found = {}
        for f, what in zip(self.fields, ("Shortcut", "Subject", "Labels")):
            if self.api and f is self.fields[0]:
                continue
            kind = names.get(f.message.objectName())
            text = f.message.text()
            if kind and text and kind not in found:
                found[kind] = f"{what}: {text}" if text == "Required." else text
        kind = "error" if "error" in found else "ok" if "ok" in found else ""
        if kind == "ok" and found["ok"].startswith("A/B test"):   # the rows already show each version
            n = self.table.rowCount()
            found["ok"] = f"A/B test: {n} versions, recipients split evenly."
        self.status.setObjectName({"error": "fieldError", "ok": "fieldOk"}.get(kind, "fieldHelp"))
        self.status.setText(found.get(kind, ""))
        self.status.setVisible(bool(kind))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class CampaignPage(Page):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.locked_banner = self.add(Banner(tm, "info", "Settings are locked while a run is in progress."))
        self.locked_banner.hide()
        self.csv_banner = self.add(Banner(tm, "danger"))
        self.csv_banner.hide()
        self.mode_banner = self.add(Banner(tm, "danger"))
        self.mode_banner.hide()

        # the check: what this campaign would do right now ------------------------
        self.metrics = ResponsiveRow(min_item_width=180)
        self.m_total = MetricCard("Recipients in list")
        self.m_eligible = MetricCard("Will be sent")
        self.m_sent = MetricCard("Sent")
        self.m_failed = MetricCard("Failed")
        for m in (self.m_total, self.m_eligible, self.m_sent, self.m_failed):
            self.metrics.add(m)
        self.add(self.metrics)

        # recipients ---------------------------------------------------------
        card = Card("Recipients")
        src_row = QHBoxLayout()
        src_row.addWidget(label("Source", "fieldLabel"))
        src_row.addSpacing(8)
        self.source = SegmentedControl([("csv", "CSV file", "file"), ("sheet", "Google Sheet", "campaign")], tm)
        src_row.addWidget(self.source)
        src_row.addStretch()
        card.body.addLayout(src_row)
        self.csv = Field("CSV file", placeholder="Choose a recipient CSV file")
        self.csv.edit.setMinimumWidth(120)
        self.csv_box = QWidget()
        row = QHBoxLayout(self.csv_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.csv, 1)
        btns = QVBoxLayout()
        btns.setSpacing(0)
        btns.addSpacing(self.csv.title.sizeHint().height() + 6)
        inner = QHBoxLayout()
        inner.setSpacing(8)
        self.browse = button("Browse…", None, "folder", tm, "text_muted")
        self.reload = button("", None, "refresh", tm, "text_muted")
        self.reload.setToolTip("Re-read the CSV")
        self.reload.setFixedWidth(36)
        inner.addWidget(self.browse)
        inner.addWidget(self.reload)
        btns.addLayout(inner)
        btns.addStretch()
        row.addLayout(btns)
        card.body.addWidget(self.csv_box)

        # Google Sheet source ------------------------------------------------
        self.sheet_box = QWidget()
        sv = QVBoxLayout(self.sheet_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(10)
        self.sheet_url = Field("Google Sheet link", "Private Sheet shared with the service account as Editor "
                               "(not 'anyone with the link').", placeholder="https://docs.google.com/spreadsheets/d/…")
        self.sheet_url.edit.setMinimumWidth(120)
        sv.addWidget(self.sheet_url)
        krow = QHBoxLayout()
        krow.setSpacing(8)
        self.google_key = Field("Service-account key file", "Keep it outside the app folder.",
                                placeholder="Choose the .json key")
        self.google_key.edit.setMinimumWidth(120)
        krow.addWidget(self.google_key, 1)
        kb = QVBoxLayout()
        kb.addSpacing(self.google_key.title.sizeHint().height() + 6)
        self.key_browse = button("Browse…", None, "folder", tm, "text_muted")
        kb.addWidget(self.key_browse)
        kb.addStretch()
        krow.addLayout(kb)
        sv.addLayout(krow)
        trow = QHBoxLayout()
        trow.setSpacing(8)
        trow.addWidget(label("Tab", "fieldLabel"))
        self.sheet_tab = QComboBox()
        self.sheet_tab.setMinimumWidth(140)
        self.sheet_tab.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        trow.addWidget(self.sheet_tab, 1)
        self.load_tabs = button("Load tabs", None, "refresh", tm, "text_muted")
        trow.addWidget(self.load_tabs)
        sv.addLayout(trow)
        hrow = QHBoxLayout()
        self.test_sheet = button("Test connection", None, "check", tm, "text_muted")
        hrow.addWidget(self.test_sheet)
        hrow.addStretch()
        sv.addLayout(hrow)
        self.sheet_info = label("", "faint", wrap=True)
        self.sheet_info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sv.addWidget(self.sheet_info)
        self.sheet_box.hide()
        card.body.addWidget(self.sheet_box)
        rows_card = Card("Row range", "Row 1 is the first row under the header, counted before any filtering, "
                         "so the numbers match the file.")
        rng = ResponsiveRow(min_item_width=200, spacing=16)
        self.row_from = Field("From row", "Blank = first row.", placeholder="First")
        self.row_to = Field("To row", "Blank = last row.", placeholder="Last")
        for f in (self.row_from, self.row_to):
            f.edit.setValidator(QIntValidator(1, 10_000_000, f.edit))
            rng.add(f)
        rows_card.body.addWidget(rng)
        retry_row = QHBoxLayout()
        self.retry_failed = button("Retry failed only", None, "refresh", tm, "text_muted")
        self.retry_failed.setToolTip("Only rows whose Status is Failed or Skipped. Unconfirmed rows are never retried.")
        self.preview = button("See the list…", None, "file", tm, "text_muted")
        self.preview.setToolTip("Per-row table of exactly what a run would do (Lists). Nothing is sent or written.")
        retry_row.addWidget(self.preview)
        retry_row.addWidget(self.retry_failed)
        retry_row.addStretch()
        card.body.addLayout(retry_row)
        self.add(card)

        # message ------------------------------------------------------------
        card = Card("Message")
        self.campaign_name = Field("Campaign name", "Saved in history: one person is never emailed twice in "
                                   "the same campaign. Blank = the subject.", placeholder="Same as the subject")
        card.body.addWidget(self.campaign_name)
        self.subject = Field("Email subject(s)", "Must exactly match a saved subject in Crisp's subject dropdown. "
                             "For an A/B test, enter several separated by | (one per shortcut, or several "
                             "with one shortcut): recipients are split evenly between them.",
                             placeholder="e.g. Subject A | Subject B")
        self.shortcut = Field("Crisp shortcut(s)", "The saved Crisp shortcut inserted as the message body. "
                              "For an A/B test, enter several separated by commas: recipients are split "
                              "evenly between them.", placeholder="e.g. !subs-small, !subs-big")
        self.shortcut_version = Field("Version label(s)", "One label per shortcut, same order (e.g. v1, v2). "
                                      "Compare them in Campaign Results. Blank = the shortcut names.",
                                      placeholder="Optional, e.g. v1, v2")
        mrow = QHBoxLayout()
        mrow.addWidget(label("Send with", "fieldLabel"))
        mrow.addSpacing(8)
        self.send_mode = SegmentedControl([("browser", "Browser", None), ("api", "Crisp API", None)], tm)
        self.send_mode.setToolTip("Browser: Crisp inbox with your saved shortcut. "
                                  "Crisp API: no browser, your message text below.")
        mrow.addWidget(self.send_mode)
        mrow.addStretch()
        card.body.addLayout(mrow)
        for f in (self.subject, self.shortcut, self.shortcut_version):   # saved texts; edited in the table
            card.body.addWidget(f)
            f.hide()
        self.versions = VersionTable(self.shortcut, self.subject, self.shortcut_version, tm)
        card.body.addWidget(self.versions)
        self.api_message_label = label("Message text", "fieldLabel")
        self.api_message = MessageEditor(tm)
        self.api_message.setPlaceholderText("Hi {first_name},\n\nYour message…")
        self.api_message.setMinimumHeight(180)
        self.api_message_help = label("Sent as the email body through the Crisp API. {name} and {first_name} "
                                      "become the recipient's name. For an A/B test, put a line with only --- "
                                      "between the versions.", "fieldHelp", wrap=True)
        self.ai_box = QWidget()
        ai = QVBoxLayout(self.ai_box)
        ai.setContentsMargins(0, 0, 0, 0)
        ai.setSpacing(6)
        self.ai_enabled = QCheckBox("Personalize the opening line with AI")
        ai.addWidget(self.ai_enabled)
        arow = QHBoxLayout()
        self.ai_generate = button("Generate with AI…", None, "send", tm, "text_muted")
        self.ai_review = button("Review in Lists", "ghost")
        arow.addWidget(self.ai_generate)
        arow.addWidget(self.ai_review)
        arow.addStretch()
        ai.addLayout(arow)
        self.ai_status = label("", "faint", wrap=True)
        ai.addWidget(self.ai_status)
        for w in (self.api_message_label, self.api_message, self.api_message_help, self.ai_box):
            card.body.addWidget(w)
            w.hide()
        self.add(card)

        # targeting ----------------------------------------------------------
        card = Card("Targeting")
        grid = ResponsiveRow(min_item_width=240, spacing=16)
        self.reference = Field("Reference filter", "Only rows whose Reference matches are sent (not case-sensitive).")
        self.segment = Field("Crisp segment", "Added to each conversation after a real send.")
        grid.add(self.reference)
        grid.add(self.segment)
        card.body.addWidget(grid)
        self.add(card)

        # advanced (collapsed) ------------------------------------------------------
        self.advanced = self.add(Collapsible("Advanced options"))
        self.advanced.add(rows_card)

        # safety ---------------------------------------------------------------
        card = Card("Safety", "Duplicate emails, invalid addresses and do-not-contact emails are always skipped "
                    "before the run starts.")
        self.dnc = Field("Do-not-contact list (optional)",
                         "A CSV with emails that must never be contacted. Any cell containing @ counts.",
                         placeholder="No list")
        self.dnc.edit.setMinimumWidth(120)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.dnc, 1)
        holder_btn = QVBoxLayout()
        holder_btn.addSpacing(self.dnc.title.sizeHint().height() + 6)
        self.dnc_browse = button("Browse…", None, "folder", tm, "text_muted")
        holder_btn.addWidget(self.dnc_browse)
        holder_btn.addStretch()
        row.addLayout(holder_btn)
        card.body.addLayout(row)
        line = QHBoxLayout()
        line.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(label("Skip failed recipients and continue", "fieldLabel"))
        texts.addWidget(label(
            "Off (default): the run stops at the first failure. On: a recipient that fails BEFORE Send is skipped "
            "and listed for retry. A failure at or after Send always stops the run.", "fieldHelp", wrap=True))
        line.addLayout(texts, 1)
        self.skip_failed = ToggleSwitch(tm)
        line.addWidget(self.skip_failed, 0, Qt.AlignVCenter)
        card.body.addLayout(line)
        limits = ResponsiveRow(min_item_width=200, spacing=16)
        self.max_skips = Field("Max skipped per run", "Stop after this many skips.")
        self.max_fails_in_row = Field("Stop after failures in a row", "Auto-stop when this many fail back to back.")
        self.recent_days = Field("Skip anyone emailed in the last N days",
                                 "All campaigns and files. Only real sends count. 0 = off: people "
                                 "can get different campaigns (never the same campaign twice).")
        self.recent_days.edit.setValidator(QIntValidator(0, 3650, self.recent_days.edit))
        limits.add(self.recent_days)
        for f in (self.max_skips, self.max_fails_in_row):
            f.edit.setValidator(QIntValidator(1, 1000, f.edit))
            limits.add(f)
        card.body.addWidget(limits)
        self.advanced.add(card)

        # sending control ------------------------------------------------------
        card = Card("Pace and timing", "The delay and the daily cap apply to all campaigns together. Times are "
                    "Dhaka time; a scheduled start and a send window need this computer on and the Crisp login "
                    "still valid.")
        grid = ResponsiveRow(min_item_width=200, spacing=16)
        self.delay = Field("Delay between emails (s)", "Default 3.")
        self.max_per_run = Field("Max emails per run", "Blank = no limit.", placeholder="No limit")
        self.daily_cap = Field("Daily cap (real sends)", "Counts today's sends in history.", placeholder="No limit")
        self.send_window = Field("Send window", "e.g. 10:00-18:00. Blank = any time.", placeholder="Any time")
        self.start_at = Field("Start at", "e.g. 10:00. Blank = start now.", placeholder="Now")
        self.delay.edit.setValidator(QIntValidator(0, 3600, self.delay.edit))
        for f in (self.max_per_run, self.daily_cap):
            f.edit.setValidator(QIntValidator(1, 100000, f.edit))
        for f in (self.delay, self.max_per_run, self.daily_cap, self.send_window, self.start_at):
            grid.add(f)
        card.body.addWidget(grid)
        line = QHBoxLayout()
        line.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(label("Random delay 20–60 s", "fieldLabel"))
        texts.addWidget(label("Waits a random 20 to 60 seconds between emails instead of the fixed delay.",
                              "fieldHelp", wrap=True))
        line.addLayout(texts, 1)
        self.random_delay = ToggleSwitch(tm)
        self.random_delay.setAccessibleName("Random delay 20 to 60 seconds")
        line.addWidget(self.random_delay, 0, Qt.AlignVCenter)
        card.body.addLayout(line)
        self.advanced.add(card)

        # Dry Run switch: shown in the top bar of the window (always visible).
        self.dry = ToggleSwitch(tm)
        self.dry.setAccessibleName("Dry Run")
        self.dry.setToolTip("Dry Run: creates each conversation and inserts the shortcut, but skips Send, the "
                            "segment, resolving and marking the list.")

        # footer -----------------------------------------------------------------
        footer = ResponsiveRow(min_item_width=150, spacing=8)
        self.reset = button("Reset to defaults", "ghost")
        self.save_as_new = button("Save as new", None, "copy", tm, "text_muted")
        self.save = button("Save campaign", None, "save", tm, "text_muted")
        self.add_queue = button("Save and add to queue", None, "play", tm, "text_muted")
        for b in (self.reset, self.save_as_new, self.save, self.add_queue):
            footer.add(b)
        self.add(footer)
        self.start = button("Start dry run", "primary", "play", tm)   # the top-bar button is the visible one
        self.start.hide()
        self.finish()

    def fields(self):
        return {"csv": self.csv, "subject": self.subject, "shortcut": self.shortcut,
                "reference": self.reference, "segment": self.segment,
                "row_from": self.row_from, "row_to": self.row_to, "dnc": self.dnc,
                "max_skips": self.max_skips, "max_fails_in_row": self.max_fails_in_row,
                "delay": self.delay, "max_per_run": self.max_per_run, "daily_cap": self.daily_cap,
                "send_window": self.send_window, "start_at": self.start_at,
                "sheet_url": self.sheet_url, "google_key": self.google_key,
                "campaign": self.campaign_name, "recent_days": self.recent_days,
                "shortcut_version": self.shortcut_version}


# =====================================================================
class ActivityPage(Page):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.error_banner = self.add(Banner(tm, "danger"))
        self.error_banner.hide()
        self.unconfirmed_banner = self.add(Banner(tm, "warning"))
        self.unconfirmed_banner.hide()
        self.retry_banner = self.add(Banner(tm, "warning"))
        self.retry_banner.hide()

        card = Card("Progress")
        self.pause = button("Pause", None, "stop", tm, "text_muted")
        self.pause.setToolTip("Pauses after the current recipient (never during a send).")
        self.pause.setEnabled(False)
        card.head_actions.addWidget(self.pause)
        self.stop = button("Stop", "dangerOutline", "stop", tm)
        self.stop.setEnabled(False)
        card.head_actions.addWidget(self.stop)
        self.step = label("Not running", "bigStep", wrap=True)
        self.recipient = label("Recipient status appears here once a run starts.", "muted", wrap=True)
        self.recipient.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.step)
        card.body.addWidget(self.recipient)
        prow = QHBoxLayout()
        prow.setSpacing(12)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.percent = label("0%", "faint")
        self.percent.setMinimumWidth(40)
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        prow.addWidget(self.progress, 1)
        prow.addWidget(self.percent)
        card.body.addLayout(prow)
        self.eta = label("", "faint", wrap=True)
        card.body.addWidget(self.eta)
        stats = QHBoxLayout()
        stats.setSpacing(36)
        self.s_done = MiniStat("Sent")
        self.s_failed = MiniStat("Failed")
        self.s_remaining = MiniStat("Remaining")
        for s in (self.s_done, self.s_failed, self.s_remaining):
            stats.addWidget(s)
        stats.addStretch()
        card.body.addLayout(stats)
        self.last_run = label("", "faint", wrap=True)
        card.body.addWidget(self.last_run)
        self.add(card)

        rows_card = Card("Recipients in this run")
        self.rows = QTableView()
        self.rows.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rows.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rows.verticalHeader().setVisible(False)
        self.rows.horizontalHeader().setStretchLastSection(True)
        self.rows.setMinimumHeight(180)
        self.rows.setWordWrap(False)
        rows_card.body.addWidget(self.rows)
        self.rows_empty = label("Each recipient appears here with its result as the run goes.", "faint", wrap=True)
        rows_card.body.addWidget(self.rows_empty)
        self.add(rows_card)

        log_card = Card("Log")
        self.copy = button("Copy", "ghost", "copy", tm, "text_muted")
        self.save = button("Save…", "ghost", "save", tm, "text_muted")
        self.clear = button("Clear", "ghost", "trash", tm, "text_muted")
        for b in (self.copy, self.save, self.clear):
            log_card.head_actions.addWidget(b)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.log.setMaximumBlockCount(20000)
        self.log.setPlaceholderText("Live output from the automation appears here.")
        self.log.setMinimumHeight(220)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        log_card.body.addWidget(self.log, 1)
        self.add(log_card, 1)


# =====================================================================
class SettingsPage(Page):
    """Grouped rows: name and explanation on the left, the control on the right.
    Long forms (Crisp API, AI) fold out under their row with Edit."""

    def __init__(self, tm: ThemeManager):
        super().__init__()

        def short(field: Field, width: int) -> Field:
            field.title.hide()
            field.setFixedWidth(width)
            return field

        # ------------------------------------------------------------ General
        group = self.add(SettingsGroup("General"))
        self.theme = SegmentedControl(
            [("light", "Light", "sun"), ("dark", "Dark", "moon"), ("system", "System", "monitor")], tm)
        group.add(SettingRow("Theme", "System follows your operating system's light or dark setting.")
                  .add_control(self.theme))
        self.runner_name = short(Field("Name", placeholder="e.g. Owadud"), 240)
        group.add(SettingRow("Your name", "Shown to teammates who send from the same Google Sheet. "
                             "Blank = this computer's name.").add_control(self.runner_name))

        # ------------------------------------------------------------ Connections
        group = self.add(SettingsGroup("Connections"))
        row = group.add(SettingRow("Crisp API", "Sends without the browser and reads delivered / read / replied. "
                                   "Token: Crisp Settings > Workspace Settings > Advanced configuration > API Token. "
                                   "Saved only on this computer."))
        form = QWidget()
        fl = QVBoxLayout(form)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(12)
        frow = ResponsiveRow(min_item_width=200, spacing=12)
        self.api_website = Field("Website ID", "Filled in from your conversation links.", placeholder="e.g. 5b6a1eda-…")
        self.api_identifier = Field("Token identifier", placeholder="Identifier")
        self.api_key = Field("Token key", placeholder="Key")
        self.api_key.edit.setEchoMode(QLineEdit.Password)
        for f in (self.api_website, self.api_identifier, self.api_key):
            frow.add(f)
        fl.addWidget(frow)
        brow = QHBoxLayout()
        self.api_save = button("Save and test", None, "check", tm, "text_muted")
        self.api_remove = button("Remove token", "ghost")
        brow.addWidget(self.api_save)
        brow.addWidget(self.api_remove)
        brow.addStretch()
        fl.addLayout(brow)
        self.api_auto = QCheckBox("Check new reads and replies every 30 minutes while the app is open "
                                  "(gives each email a 'Read at' time within about 30 minutes)")
        self.api_auto.setChecked(True)
        fl.addWidget(self.api_auto)
        fl.addWidget(label("Send a test email through the API", "fieldLabel"))
        fl.addWidget(label("Sends ONE real email to the address you type (use your own), to check that "
                           "Crisp delivers API messages by email before campaigns use the API.", "fieldHelp", wrap=True))
        trow = ResponsiveRow(min_item_width=220, spacing=12)
        self.api_test_to = Field("To (your own email)", placeholder="you@example.com")
        self.api_test_subject = Field("Subject", placeholder="Test from Crisp Automation")
        trow.add(self.api_test_to)
        trow.add(self.api_test_subject)
        fl.addWidget(trow)
        self.api_test_text = QPlainTextEdit()
        self.api_test_text.setPlaceholderText("Message text")
        self.api_test_text.setPlainText("Hi, this is a test message sent through the Crisp API by Crisp Automation.")
        self.api_test_text.setFixedHeight(80)
        fl.addWidget(self.api_test_text)
        t2 = QHBoxLayout()
        self.api_test_send = button("Send test email", None, "send", tm, "text_muted")
        self.api_test_open = button("Open in Crisp", "ghost")
        self.api_test_open.hide()
        t2.addWidget(self.api_test_send)
        t2.addWidget(self.api_test_open)
        t2.addStretch()
        fl.addLayout(t2)
        self.api_test_result = label("", "faint", wrap=True)
        self.api_test_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fl.addWidget(self.api_test_result)
        row.set_details(form)
        self.api_status = row.add_below(label("", "faint", wrap=True))
        self.api_row = row

        row = group.add(SettingRow("AI for opening lines", "Claude, ChatGPT, Gemini, a free one (Groq, OpenRouter, "
                                   "Ollama on this computer) or any OpenAI-compatible service. The AI receives your "
                                   "subject and message, and per person only a first name and the company from the "
                                   "email domain. The key is saved only on this computer."))
        form = QWidget()
        fl = QVBoxLayout(form)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(12)
        prow = QVBoxLayout()
        prow.setSpacing(6)
        prow.addWidget(label("Provider", "fieldLabel"))
        self.ai_provider = QComboBox()
        self.ai_provider.setMaximumWidth(520)
        prow.addWidget(self.ai_provider)
        self.ai_provider_help = label("", "fieldHelp", wrap=True)
        prow.addWidget(self.ai_provider_help)
        fl.addLayout(prow)
        self.ai_key = Field("API key", placeholder="Paste the key")
        self.ai_key.edit.setEchoMode(QLineEdit.Password)
        self.ai_key.setMaximumWidth(520)
        fl.addWidget(self.ai_key)
        self.ai_base_url = Field("Base URL", "OpenAI-compatible address, e.g. https://api.example.com/v1",
                                 placeholder="https://…/v1")
        self.ai_base_url.setMaximumWidth(520)
        fl.addWidget(self.ai_base_url)
        self.ai_model = Field("Model", "Click Load models to pick from the provider's current list.",
                              placeholder="Model id")
        self.ai_model.setMaximumWidth(520)
        fl.addWidget(self.ai_model)
        mrow = QHBoxLayout()
        self.ai_load_models = button("Load models", None, "refresh", tm, "text_muted")
        self.ai_models = QComboBox()
        self.ai_models.setVisible(False)
        self.ai_models.setMinimumWidth(260)
        mrow.addWidget(self.ai_load_models)
        mrow.addWidget(self.ai_models)
        mrow.addStretch()
        fl.addLayout(mrow)
        krow = QHBoxLayout()
        self.ai_save = button("Save and test", None, "check", tm, "text_muted")
        self.ai_remove = button("Remove key", "ghost")
        krow.addWidget(self.ai_save)
        krow.addWidget(self.ai_remove)
        krow.addStretch()
        fl.addLayout(krow)
        row.set_details(form)
        self.ai_key_status = row.add_below(label("", "faint", wrap=True))
        self.ai_row = row

        # ------------------------------------------------------------ Sending
        group = self.add(SettingsGroup("Sending"))
        self.parallel = short(Field("Campaigns at the same time", placeholder="1"), 90)
        group.add(SettingRow("Campaigns at the same time",
                             "1 = one after another in your Crisp browser (the safe default). 2 or 3 run together, "
                             "each in its own browser window with its own Crisp login. The delay and daily cap still "
                             "count for all of them together, so it is not faster.").add_control(self.parallel))
        self.claim_batch = short(Field("Batch size", placeholder="10"), 90)
        group.add(SettingRow("Shared Sheet: batch size",
                             "Recipients each runner claims at a time, so teammates sending from the same Sheet "
                             "never email the same person.").add_control(self.claim_batch))
        self.claim_expiry = short(Field("Claim expiry (minutes)", placeholder="5"), 90)
        group.add(SettingRow("Shared Sheet: claim expiry (minutes)",
                             "Unsent claims of a runner that stopped responding are freed after this.")
                  .add_control(self.claim_expiry))

        # ------------------------------------------------------------ This computer
        group = self.add(SettingsGroup("This computer"))
        self.make_shortcut = button("Create shortcut", None, "send", tm, "text_muted")
        row = group.add(SettingRow("Desktop icon", "Adds Crisp Automation to the Desktop and app menu, so it opens "
                                   "with one click and no terminal. Clicking it while the app is open (even during "
                                   "a campaign) brings the open window to the front.").add_control(self.make_shortcut))
        self.shortcut_status = row.add_below(label("", "faint", wrap=True))
        self.open_profile = button("Open folder", None, "folder", tm, "text_muted")
        self.copy_profile = button("Copy path", None, "copy", tm, "text_muted")
        row = group.add(SettingRow("Browser session", "Your Crisp login is saved in this Chromium profile, so you "
                                   "sign in once. It never leaves this computer.")
                        .add_control(self.open_profile, self.copy_profile))
        self.profile_path = row.add_below(label("", "kvValue", wrap=True))
        self.profile_path.setMinimumWidth(1)
        self.profile_path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.profile_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.profile_state = row.add_below(label("", "faint"))
        self.check_crisp = button("Check Crisp", None, "check", tm, "text_muted")
        row = group.add(SettingRow("Check Crisp", "Opens Crisp and looks for the subject dropdown, the Shortcuts tab "
                                   "and the segment field. Nothing is created or sent.").add_control(self.check_crisp))
        self.health_result = row.add_below(label("", "faint", wrap=True))
        self.health_result.setTextInteractionFlags(Qt.TextSelectableByMouse)

        # ------------------------------------------------------------ App
        group = self.add(SettingsGroup("App"))
        self.check_updates = button("Check for updates", None, "refresh", tm, "text_muted")
        self.install_update = button("Update now", "primary")
        self.install_update.hide()
        row = group.add(SettingRow("Updates", "New versions download from GitHub. Your settings, lists, history, "
                                   "Crisp login and logs are never changed; the old code is kept in backups/.")
                        .add_control(self.check_updates, self.install_update))
        self.update_status = row.add_below(label("Not checked yet.", "faint", wrap=True))
        self.updates_slot = QVBoxLayout()            # kept for older code; Updates now lives in the App group
        self.reset = button("Reset campaign fields", "dangerOutline")
        group.add(SettingRow("Campaign defaults", "Restore the subject, shortcut, segment, reference and CSV to "
                             "the original values.").add_control(self.reset))
        row = group.add(SettingRow("About"))
        self.about = row.add_below(KeyValueList())
        for key, title in (("version", "Version"), ("agent", "Agent script"),
                           ("python", "Python"), ("config", "Preferences file")):
            self.about.add(key, title)
        self.about.set("version", f"Crisp Automation Desktop {APP_VERSION}")
        self.finish()

# =====================================================================
class HistoryPage(Page):
    COLUMNS = ("Started", "Mode", "Result", "Sent", "Failed", "Skipped")

    def __init__(self, tm: ThemeManager):
        super().__init__()
        card = Card("Runs", "One folder per run: run.log, the CSV before and after, settings, report.csv and "
                    "failure screenshots. Runs older than 30 days are deleted automatically.")
        self.refresh = button("", None, "refresh", tm, "text_muted")
        self.refresh.setToolTip("Reload")
        self.refresh.setFixedWidth(36)
        card.head_actions.addWidget(self.refresh)
        self.sent7 = label("", "muted", wrap=True)
        card.body.addWidget(self.sent7)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(280)
        card.body.addWidget(self.table)
        self.empty = label("No runs yet. Each run you start gets its own folder here.", "faint", wrap=True)
        card.body.addWidget(self.empty)
        btns = QHBoxLayout()
        self.open_run = button("Open folder", None, "folder", tm, "text_muted")
        self.export_report = button("Export report…", None, "save", tm, "text_muted")
        self.open_runs = button("All runs", "ghost", "folder", tm, "text_muted")
        card.head_actions.insertWidget(0, self.open_runs)
        for b in (self.open_run, self.export_report):
            btns.addWidget(b)
        btns.addStretch()
        card.body.addLayout(btns)
        self.add(card)

        card = Card("Diagnostics", "One zip for asking for help: the log with emails masked, versions, settings "
                    "and a self-test. It never includes your Crisp login, CSV files or screenshots.")
        h = QHBoxLayout()
        self.copy_diag = button("Copy diagnostics", None, "copy", tm, "text_muted")
        h.addWidget(self.copy_diag)
        h.addStretch()
        card.body.addLayout(h)
        self.diag_status = label("", "faint", wrap=True)
        self.diag_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.diag_status)
        self.add(card)
        self.finish()


# =====================================================================
def _table(columns, min_height=260) -> QTableWidget:
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    t.horizontalHeader().setStretchLastSection(True)
    t.setSortingEnabled(True)
    t.setMinimumHeight(min_height)
    return t


class CampaignsPage(Page):
    """Saved campaigns and the run queue. Actions shown depend on the selected campaign."""

    COLUMNS = ("#", "Campaign", "Status", "List", "Subject", "Shortcut(s)", "Last run", "Note")

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.banner = self.add(Banner(tm, "warning", ""))
        self.banner.hide()

        head = QHBoxLayout()
        head.setSpacing(8)
        self.new = button("New campaign", None, "campaign", tm, "text_muted")
        self.stop_queue = button("Stop queue", None, "stop", tm, "text_muted")
        self.run_queue = button("Run queue", "primary", "play", tm)
        head.addWidget(self.new)
        head.addStretch()
        head.addWidget(self.stop_queue)
        head.addWidget(self.run_queue)
        holder = QWidget()
        holder.setLayout(head)
        self.add(holder)
        self.queue_status = self.add(label("", "muted", wrap=True))

        card = Card("Campaigns")
        self.table = _table(self.COLUMNS, min_height=280)
        self.table.setSortingEnabled(False)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setMinimumWidth(200)
        card.body.addWidget(self.table)
        self.empty = label("No campaigns yet. Click New campaign, fill it in, then Save campaign.", "faint", wrap=True)
        card.body.addWidget(self.empty)

        # actions for the selected campaign (only the ones that apply are shown)
        self.actions = ResponsiveRow(min_item_width=140, spacing=8)
        self.open_sel = button("Open", None, "campaign", tm, "text_muted")
        self.enqueue = button("Add to queue", None, "play", tm, "text_muted")
        self.dequeue = button("Remove from queue", None, "stop", tm, "text_muted")
        self.up = button("Move up", None)
        self.down = button("Move down", None)
        self.run_next = button("Run next", None)
        self.pause = button("Pause", None)
        self.stop = button("Stop campaign", "dangerOutline", "stop", tm)
        self.checked = button("I checked Crisp", None, "check", tm, "text_muted")
        self.schedule = button("Schedule…", None, "play", tm, "text_muted")
        self.unschedule = button("Cancel schedule", None, "stop", tm, "text_muted")
        self.duplicate = button("Duplicate", None, "copy", tm, "text_muted")
        self.delete = button("Delete", "dangerOutline", "trash", tm)
        self.action_buttons = (self.open_sel, self.schedule, self.unschedule, self.enqueue, self.dequeue, self.up,
                               self.down, self.run_next, self.pause, self.stop, self.checked, self.duplicate,
                               self.delete)
        for b in self.action_buttons:
            self.actions.add(b)
        card.body.addWidget(self.actions)
        self.add(card)
        self.finish()

    def selected_id(self) -> str:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return ""
        item = self.table.item(rows[0].row(), 1)
        return item.data(Qt.UserRole) if item else ""


class ResultsPage(Page):
    """Campaign Results: read-only statuses from Crisp, per campaign."""

    def __init__(self, tm: ThemeManager):
        super().__init__()
        card = Card("Campaign Results", "Statuses are read from Crisp with your saved login. Read-only: "
                    "nothing is typed, sent, resolved or changed. Pages are opened slowly (one every few seconds).")
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Campaign", "fieldLabel"))
        self.campaign = QComboBox()
        self.campaign.setMinimumWidth(160)
        self.campaign.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        row.addWidget(self.campaign, 1)
        self.refresh = button("Refresh statuses", "primary", "refresh", tm)
        self.runs = button("Runs and diagnostics", "ghost", "folder", tm, "text_muted")
        self.import_sends = button("Import or sync…", "ghost", "file", tm, "text_muted")
        self.import_sends.setToolTip("Add sends recorded in any CSV or Google Sheet tab (Mail Sent, Sent At, "
                                     "Conversation link), e.g. from another computer or an older version.")
        self.sync_now = button("Sync now", "ghost", "refresh", tm, "text_muted")
        self.synced = button("Synced lists", "ghost", "campaign", tm, "text_muted")
        self.stop = button("Stop", "dangerOutline", "stop", tm)
        self.stop.hide()
        row.addWidget(self.refresh)
        row.addWidget(self.stop)
        card.body.addLayout(row)
        links = ResponsiveRow(min_item_width=200, spacing=8)     # wraps on narrow windows
        for b in (self.import_sends, self.sync_now, self.synced, self.runs):
            links.add(b)
        card.body.addWidget(links)
        self.sync_info = label("", "faint", wrap=True)
        card.body.addWidget(self.sync_info)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        card.body.addWidget(self.progress)
        self.progress_text = label("", "faint", wrap=True)
        card.body.addWidget(self.progress_text)
        self.last_checked = label("", "muted", wrap=True)
        card.body.addWidget(self.last_checked)
        self.suggest = Banner(tm, "info")
        self.suggest.hide()
        card.body.addWidget(self.suggest)
        self.add(card)

        self.metrics = ResponsiveRow(min_item_width=150)
        self.m_sent = MetricCard("Sent")
        self.m_delivered = MetricCard("Delivered")
        self.m_read = MetricCard("Read (approx.)")
        self.m_replied = MetricCard("Replied")
        self.m_failed = MetricCard("Failed")
        for m in (self.m_sent, self.m_delivered, self.m_read, self.m_replied, self.m_failed):
            self.metrics.add(m)
        self.add(self.metrics)
        self.summary = label("", "muted", wrap=True)
        self.summary.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.add(self.summary)
        self.read_note = label("", "faint", wrap=True)
        self.add(self.read_note)

        card = Card("Details")
        self.tabs = QTabWidget()

        # recipients ------------------------------------------------------
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 8, 0, 0)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.f_status = QComboBox()
        self.f_version = QComboBox()
        self.f_date = QComboBox()
        self.f_date.addItems(["Any time", "Last 24 hours", "Last 7 days", "Last 30 days"])
        for w in (self.f_status, self.f_version, self.f_date):
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            w.setMinimumWidth(110)
            filters.addWidget(w, 1)
        self.export = button("Export CSV…", None, "save", tm, "text_muted")
        filters.addWidget(self.export)
        v.addLayout(filters)
        self.table = _table(["Email", "Name", "Status", "Sent", "Delivered", "Read", "Replied",
                             "Shortcut version", "Campaign", "Conversation link", "Note"], 320)
        v.addWidget(self.table)
        v.addWidget(label("Click a conversation link to open it. \"first seen\" = Crisp shows no time for "
                          "this step, so the time we first noticed it is shown.", "faint", wrap=True))
        self.tabs.addTab(tab, "Recipients")

        # needs follow-up ---------------------------------------------------
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(label("Customers who replied and whose conversation is still open.", "faint", wrap=True))
        self.followup = _table(["Email", "Name", "Replied", "Campaign", "Shortcut version", "Conversation link"])
        v.addWidget(self.followup)
        self.tabs.addTab(tab, "Needs follow-up")

        # delivery problems ---------------------------------------------------
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 8, 0, 0)
        self.problems = _table(["Email", "Name", "Reason", "Sent", "Campaign", "Conversation link"])
        v.addWidget(self.problems)
        h = QHBoxLayout()
        self.add_dnc = button("Add to do-not-contact…", None, "alert", tm, "text_muted")
        self.add_dnc.setToolTip("Adds these emails to your do-not-contact list after you confirm (never automatic).")
        h.addWidget(self.add_dnc)
        h.addStretch()
        v.addLayout(h)
        self.tabs.addTab(tab, "Delivery problems")

        # compare ---------------------------------------------------------------
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(label("Campaigns side by side", "fieldLabel"))
        self.compare_campaigns = _table(["Campaign", "Sent", "Delivered %", "Read % (approx.)", "Reply %",
                                         "Failed"], 160)
        v.addWidget(self.compare_campaigns)
        v.addWidget(label("Shortcut versions in the selected campaign", "fieldLabel"))
        self.compare_versions = _table(["Shortcut version", "Sent", "Delivered %", "Read % (approx.)",
                                        "Reply %", "Failed"], 120)
        v.addWidget(self.compare_versions)
        self.tabs.addTab(tab, "Compare")

        # check history --------------------------------------------------------
        tab = QWidget()
        v = QVBoxLayout(tab)
        v.setContentsMargins(0, 8, 0, 0)
        v.addWidget(label("Each refresh is saved, so you can see read and reply numbers grow.", "faint", wrap=True))
        self.checks = _table(["Checked at", "Campaign", "Sent", "Delivered", "Read (approx.)", "Replied",
                              "Failed"], 200)
        v.addWidget(self.checks)
        self.tabs.addTab(tab, "Check history")

        card.body.addWidget(self.tabs)
        self.add(card, 1)
        self.finish()


__all__ = ["DashboardPage", "CampaignPage", "ActivityPage", "SettingsPage", "HistoryPage", "ResultsPage"]


# =====================================================================
def _view(min_height=300) -> QTableView:
    v = QTableView()
    v.setSelectionBehavior(QAbstractItemView.SelectRows)
    v.setSelectionMode(QAbstractItemView.SingleSelection)
    v.setEditTriggers(QAbstractItemView.NoEditTriggers)
    v.verticalHeader().setVisible(False)
    v.horizontalHeader().setStretchLastSection(True)
    v.setWordWrap(False)
    v.setMinimumHeight(min_height)
    return v


class ListsPage(Page):
    """The recipients of the campaign open in the editor: what a run would do, row by row."""

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.error = self.add(Banner(tm, "danger"))
        self.error.hide()
        card = Card("Recipients")
        self.source = label("", "muted", wrap=True)
        self.summary = label("", "kvValue", wrap=True)
        card.body.addWidget(self.source)
        card.body.addWidget(self.summary)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("Show", "fieldLabel"))
        self.filter = SegmentedControl([("all", "All", None), ("send", "Will send", None),
                                        ("skip", "Skipped", None)], tm)
        self.filter.set_value("all")
        row.addWidget(self.filter)
        row.addStretch()
        self.refresh = button("Re-read", None, "refresh", tm, "text_muted")
        row.addWidget(self.refresh)
        card.body.addLayout(row)
        self.table = _view()
        card.body.addWidget(self.table)
        self.empty = label("Choose a recipient list in the campaign (Campaigns > Open).", "faint", wrap=True)
        card.body.addWidget(self.empty)
        self.add(card, 1)


class TeamPage(Page):
    """Who else is sending from the same Google Sheet (from the claims log)."""

    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.info = self.add(Banner(tm, "info", ""))
        card = Card("Team activity")
        self.refresh = button("Refresh", None, "refresh", tm, "text_muted")
        card.head_actions.addWidget(self.refresh)
        self.status = label("", "muted", wrap=True)
        card.body.addWidget(self.status)
        self.table = _view(180)
        card.body.addWidget(self.table)
        self.add(card)
        card = Card("Needs a check in Crisp", "A computer stopped during or after a Send click. These are never "
                    "retried automatically. Open Crisp, look for the conversation, then record what you found.")
        self.checks = _view(140)
        card.body.addWidget(self.checks)
        row = QHBoxLayout()
        self.mark_sent = button("It was sent", None, "check", tm, "text_muted")
        self.mark_not_sent = button("It was not sent", "dangerOutline")
        row.addWidget(self.mark_sent)
        row.addWidget(self.mark_not_sent)
        row.addStretch()
        card.body.addLayout(row)
        self.add(card)
        self.finish()

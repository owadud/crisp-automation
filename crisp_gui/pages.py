"""The pages. They only build widgets and expose them;
MainWindow wires behaviour so all logic lives in one place."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QComboBox,
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QPlainTextEdit, QProgressBar, QScrollArea,
    QSizePolicy, QTabWidget, QTableView, QTableWidget, QVBoxLayout, QWidget,
)

from .config import APP_VERSION
from .theme import ThemeManager
from .widgets import (
    Banner, Card, Collapsible, Field, KeyValueList, MetricCard, ResponsiveRow,
    SegmentedControl, ToggleSwitch, button, label,
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
        self.subject = Field("Email subject", "Must exactly match a saved subject in Crisp's subject dropdown.")
        self.shortcut = Field("Crisp shortcut(s)", "The saved Crisp shortcut inserted as the message body. "
                              "For an A/B test, enter several separated by commas: recipients are split "
                              "evenly between them.", placeholder="e.g. !subs-small, !subs-big")
        self.shortcut_version = Field("Version label(s)", "One label per shortcut, same order (e.g. v1, v2). "
                                      "Compare them in Campaign Results. Blank = the shortcut names.",
                                      placeholder="Optional, e.g. v1, v2")
        card.body.addWidget(self.subject)
        card.body.addWidget(self.shortcut)
        card.body.addWidget(self.shortcut_version)
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
                                 "All campaigns and files. 0 = off. Only real sends count.")
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
    def __init__(self, tm: ThemeManager):
        super().__init__()
        card = Card("Appearance")
        row = QHBoxLayout()
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(label("Theme", "fieldLabel"))
        texts.addWidget(label("System follows your operating system's light or dark setting.", "fieldHelp", wrap=True))
        row.addLayout(texts, 1)
        self.theme = SegmentedControl(
            [("light", "Light", "sun"), ("dark", "Dark", "moon"), ("system", "System", "monitor")], tm)
        row.addWidget(self.theme, 0, Qt.AlignVCenter)
        card.body.addLayout(row)
        self.add(card)

        card = Card("Your name", "Shown to teammates who send from the same Google Sheet.")
        self.runner_name = Field("Name", "Blank = this computer's name.", placeholder="e.g. Owadud")
        self.runner_name.setMaximumWidth(360)
        card.body.addWidget(self.runner_name)
        self.add(card)

        self.updates_slot = QVBoxLayout()            # Updates card goes here (filled below)
        self.layout_.addLayout(self.updates_slot)

        self.advanced = self.add(Collapsible("Advanced"))

        card = Card("Browser session", "Your Crisp login is saved in this Chromium profile, so you only sign in once. "
                    "It stays on this computer and is never part of the project files.")
        self.profile_path = label("", "kvValue", wrap=True)
        self.profile_path.setMinimumWidth(1)
        self.profile_path.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.profile_path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.profile_state = label("", "faint")
        card.body.addWidget(self.profile_path)
        card.body.addWidget(self.profile_state)
        btns = QHBoxLayout()
        self.open_profile = button("Open folder", None, "folder", tm, "text_muted")
        self.copy_profile = button("Copy path", None, "copy", tm, "text_muted")
        btns.addWidget(self.open_profile)
        btns.addWidget(self.copy_profile)
        btns.addStretch()
        card.body.addLayout(btns)
        self.advanced.add(card)

        card = Card("Shared Google Sheet", "Teammates can send from the same Sheet at the same time. "
                    "Each recipient is claimed before sending, so nobody is emailed twice.")
        row = ResponsiveRow(min_item_width=180)
        self.claim_batch = Field("Batch size", "Recipients claimed at a time.", placeholder="10")
        self.claim_expiry = Field("Claim expiry (minutes)", "Unsent claims of a runner that stopped "
                                  "responding are freed after this.", placeholder="5")
        for f in (self.claim_batch, self.claim_expiry):
            row.add(f)
        card.body.addWidget(row)
        self.advanced.add(card)

        card = Card("Campaign queue", "1 = campaigns run one after another in your Crisp browser (the safe "
                    "default). 2 or 3 run at the same time, each in its own browser window with its own Crisp "
                    "login (sign in once per window). They all use YOUR Crisp account: the delay between emails "
                    "and the daily cap still count for all of them together, so it is not faster.")
        self.parallel = Field("Campaigns at the same time", "1, 2 or 3.", placeholder="1")
        self.parallel.setMaximumWidth(260)
        card.body.addWidget(self.parallel)
        self.advanced.add(card)

        card = Card("Campaign defaults", "Restore the subject, shortcut, segment, reference and CSV to the original values.")
        self.reset = button("Reset campaign fields", "dangerOutline")
        h = QHBoxLayout()
        h.addWidget(self.reset)
        h.addStretch()
        card.body.addLayout(h)
        self.advanced.add(card)

        card = Card("Updates", "New versions download from GitHub. Your settings, CSV files, history, "
                    "Crisp login and logs are never changed. The old code is kept in backups/.")
        self.update_status = label("Not checked yet.", "faint", wrap=True)
        card.body.addWidget(self.update_status)
        h = QHBoxLayout()
        self.check_updates = button("Check for updates", None, "refresh", tm, "text_muted")
        self.install_update = button("Update now", "primary")
        self.install_update.hide()
        h.addWidget(self.check_updates)
        h.addWidget(self.install_update)
        h.addStretch()
        card.body.addLayout(h)
        self.updates_slot.addWidget(card)

        card = Card("Check Crisp", "Opens Crisp and looks for the subject dropdown, the Shortcuts tab and the "
                    "segment field. Nothing is created or sent. When asked, open any existing conversation.")
        h = QHBoxLayout()
        self.check_crisp = button("Check Crisp", None, "check", tm, "text_muted")
        h.addWidget(self.check_crisp)
        h.addStretch()
        card.body.addLayout(h)
        self.health_result = label("", "faint", wrap=True)
        self.health_result.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.body.addWidget(self.health_result)
        self.advanced.add(card)

        card = Card("About")
        self.about = KeyValueList()
        for key, title in (("version", "Version"), ("agent", "Agent script"),
                           ("python", "Python"), ("config", "Preferences file")):
            self.about.add(key, title)
        self.about.set("version", f"Crisp Automation Desktop {APP_VERSION}")
        card.body.addWidget(self.about)
        self.advanced.add(card)
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
        self.duplicate = button("Duplicate", None, "copy", tm, "text_muted")
        self.delete = button("Delete", "dangerOutline", "trash", tm)
        self.action_buttons = (self.open_sel, self.enqueue, self.dequeue, self.up, self.down, self.run_next,
                               self.pause, self.stop, self.checked, self.duplicate, self.delete)
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
        self.stop = button("Stop", "dangerOutline", "stop", tm)
        self.stop.hide()
        row.addWidget(self.refresh)
        row.addWidget(self.stop)
        card.body.addLayout(row)
        card.head_actions.addWidget(self.runs)
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

"""The four pages. They only build widgets and expose them;
MainWindow wires behaviour so all logic lives in one place."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QPlainTextEdit, QProgressBar, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from .config import APP_VERSION
from .theme import ThemeManager
from .widgets import (
    Banner, Card, Field, KeyValueList, MetricCard, ResponsiveRow,
    SegmentedControl, ToggleSwitch, button, divider, label,
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
class DashboardPage(Page):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.csv_banner = self.add(Banner(tm, "danger"))
        self.csv_banner.hide()
        self.unconfirmed_banner = self.add(Banner(tm, "warning"))
        self.unconfirmed_banner.hide()

        self.metrics = ResponsiveRow(min_item_width=180)
        self.m_total = MetricCard("Recipients in CSV")
        self.m_eligible = MetricCard("Eligible to send")
        self.m_sent = MetricCard("Sent")
        self.m_failed = MetricCard("Failed")
        for m in (self.m_total, self.m_eligible, self.m_sent, self.m_failed):
            self.metrics.add(m)
        self.add(self.metrics)

        row = ResponsiveRow(min_item_width=340, spacing=16)

        # current run --------------------------------------------------
        self.run_card = Card("Current run")
        self.open_logs = button("View activity", "ghost", "logs", tm, "text_muted")
        self.run_card.head_actions.addWidget(self.open_logs)
        self.run_step = label("No run yet", "bigStep", wrap=True)
        self.run_detail = label("Start a dry run to check the campaign end to end.", "muted", wrap=True)
        self.run_progress = QProgressBar()
        self.run_progress.setRange(0, 1000)
        self.run_progress.setTextVisible(False)
        self.run_progress_text = label("", "faint")
        self.run_card.body.addWidget(self.run_step)
        self.run_card.body.addWidget(self.run_detail)
        self.run_card.body.addWidget(self.run_progress)
        self.run_card.body.addWidget(self.run_progress_text)
        self.run_card.body.addStretch()
        self.last_run = label("", "faint", wrap=True)
        self.run_card.body.addWidget(divider())
        self.run_card.body.addWidget(self.last_run)

        # campaign summary ------------------------------------------------
        self.campaign_card = Card("Campaign")
        self.edit_campaign = button("Edit", "ghost", "campaign", tm, "text_muted")
        self.campaign_card.head_actions.addWidget(self.edit_campaign)
        self.kv = KeyValueList()
        for key, title in (("csv", "CSV file"), ("subject", "Subject"), ("shortcut", "Shortcut"),
                           ("segment", "Segment"), ("reference", "Reference"), ("mode", "Mode")):
            self.kv.add(key, title)
        self.campaign_card.body.addWidget(self.kv)
        self.campaign_card.body.addStretch()

        row.add(self.run_card)
        row.add(self.campaign_card)
        self.add(row)
        self.finish()


# =====================================================================
class CampaignPage(Page):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.locked_banner = self.add(Banner(tm, "info", "Settings are locked while a run is in progress."))
        self.locked_banner.hide()

        # recipients ---------------------------------------------------------
        card = Card("Recipients", "A CSV with Full Name, Contact Email, Reference and Mail Sent columns.")
        self.csv = Field("CSV file", placeholder="Choose a recipient CSV file")
        self.csv.edit.setMinimumWidth(120)
        row = QHBoxLayout()
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
        card.body.addLayout(row)
        rng = ResponsiveRow(min_item_width=200, spacing=16)
        self.row_from = Field("From row", "Blank = first row.", placeholder="First")
        self.row_to = Field("To row", "Blank = last row.", placeholder="Last")
        for f in (self.row_from, self.row_to):
            f.edit.setValidator(QIntValidator(1, 10_000_000, f.edit))
            rng.add(f)
        card.body.addWidget(rng)
        card.body.addWidget(label("Row 1 is the first row under the header, counted before any filtering, "
                                  "so the numbers match the CSV file.", "fieldHelp", wrap=True))
        self.add(card)

        # message ------------------------------------------------------------
        card = Card("Message")
        self.subject = Field("Email subject", "Must exactly match a saved subject in Crisp's subject dropdown.")
        self.shortcut = Field("Crisp shortcut", "The saved Crisp shortcut inserted as the message body.")
        card.body.addWidget(self.subject)
        card.body.addWidget(self.shortcut)
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

        # mode -----------------------------------------------------------------
        card = Card("Sending mode")
        line = QHBoxLayout()
        line.setSpacing(16)
        texts = QVBoxLayout()
        texts.setSpacing(3)
        texts.addWidget(label("Dry Run", "fieldLabel"))
        texts.addWidget(label(
            "Creates each conversation and inserts the shortcut, but skips Send, the segment, "
            "resolving and the CSV update.", "fieldHelp", wrap=True))
        line.addLayout(texts, 1)
        self.dry = ToggleSwitch(tm)
        line.addWidget(self.dry, 0, Qt.AlignVCenter)
        card.body.addLayout(line)
        self.mode_banner = Banner(tm, "danger")
        card.body.addWidget(self.mode_banner)
        self.add(card)

        # footer -----------------------------------------------------------------
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        self.reset = button("Reset to defaults", "ghost")
        footer.addWidget(self.reset)
        footer.addStretch()
        self.start = button("Start dry run", "primary", "play", tm)
        footer.addWidget(self.start)
        holder = QWidget()
        holder.setLayout(footer)
        self.add(holder)
        self.finish()

    def fields(self):
        return {"csv": self.csv, "subject": self.subject, "shortcut": self.shortcut,
                "reference": self.reference, "segment": self.segment,
                "row_from": self.row_from, "row_to": self.row_to}


# =====================================================================
class ActivityPage(Page):
    def __init__(self, tm: ThemeManager):
        super().__init__()
        self.error_banner = self.add(Banner(tm, "danger"))
        self.error_banner.hide()
        self.unconfirmed_banner = self.add(Banner(tm, "warning"))
        self.unconfirmed_banner.hide()

        card = Card("Progress")
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
        stats = QHBoxLayout()
        stats.setSpacing(36)
        self.s_done = MiniStat("Sent")
        self.s_failed = MiniStat("Failed")
        self.s_remaining = MiniStat("Remaining")
        for s in (self.s_done, self.s_failed, self.s_remaining):
            stats.addWidget(s)
        stats.addStretch()
        card.body.addLayout(stats)
        self.add(card)

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
        self.log.setMinimumHeight(300)
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

        card = Card("Browser session", "Your Crisp login is saved in this Chromium profile, so you only sign in once. "
                    "It stays on this computer and is never part of the project files.")
        self.profile_path = label("", "kvValue", wrap=True)
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
        self.add(card)

        card = Card("Campaign defaults", "Restore the subject, shortcut, segment, reference and CSV to the original values.")
        self.reset = button("Reset campaign fields", "dangerOutline")
        h = QHBoxLayout()
        h.addWidget(self.reset)
        h.addStretch()
        card.body.addLayout(h)
        self.add(card)

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
        self.add(card)

        card = Card("About")
        self.about = KeyValueList()
        for key, title in (("version", "Version"), ("agent", "Agent script"),
                           ("python", "Python"), ("config", "Preferences file")):
            self.about.add(key, title)
        self.about.set("version", f"Crisp Automation Desktop {APP_VERSION}")
        card.body.addWidget(self.about)
        self.add(card)
        self.finish()


__all__ = ["DashboardPage", "CampaignPage", "ActivityPage", "SettingsPage"]

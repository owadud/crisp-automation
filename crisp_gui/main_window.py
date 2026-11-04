"""Main window: sidebar navigation, top bar, pages, and all behaviour."""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import send_control as sc  # noqa: E402

from PySide6.QtCore import QProcess, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QTableWidgetItem,
    QApplication, QButtonGroup, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QStackedWidget, QVBoxLayout, QWidget,
)

from . import run_state as rs
from . import widgets as W
from .agent_process import AgentRunner
from .config import AGENT_SCRIPT, APP_NAME, APP_VERSION, GITHUB_REPO, PROFILE_DIR, ROOT, Preferences
from .csv_stats import CsvStats, read_stats
from .icons import icon, logo_pixmap
from .diagnostics import build_zip
from .pages import ActivityPage, CampaignPage, DashboardPage, HistoryPage, SettingsPage
from .theme import Palette, ThemeManager
from .update_controller import UpdateController

PAGES = (
    ("dashboard", "Dashboard", "dashboard", "Overview of your campaign and the latest run."),
    ("campaign", "Campaign Setup", "campaign", "Recipients, message and sending mode."),
    ("activity", "Activity Logs", "logs", "Live progress and output from the automation."),
    ("settings", "Settings", "settings", "Appearance, browser session and defaults."),
    ("history", "History", "file", "Past runs, reports and diagnostics."),
)

STATUS = {
    rs.IDLE: ("Ready", "neutral"),
    rs.STARTING: ("Starting", "accent"),
    rs.WAITING_LOGIN: ("Waiting for login", "warning"),
    rs.RUNNING: ("Running", "accent"),
    rs.STOPPING: ("Stopping", "warning"),
    rs.FINISHED: ("Finished", "success"),
    rs.FAILED: ("Failed", "danger"),
    rs.STOPPED: ("Stopped", "neutral"),
}


class MainWindow(QMainWindow):
    def __init__(self, tm: ThemeManager, prefs: Preferences | None = None,
                 agent_script: Path | None = None, profile_dir: Path | None = None,
                 data_dir: Path | None = None):
        super().__init__()
        self.tm = tm
        # history.csv and runs/ live here (the app folder; never touched by updates).
        self.data_dir = Path(data_dir or ROOT)
        self.runs_root = self.data_dir / "runs"
        self.history_file = self.data_dir / "history.csv"
        self.run_dir: Path | None = None
        self._run_log = None
        self._sent7 = 0
        self.prefs = prefs or Preferences()
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.runner = AgentRunner(agent_script or AGENT_SCRIPT, self)
        self.updater = UpdateController(GITHUB_REPO, ROOT, APP_VERSION, self)
        self.stats = CsvStats()
        self.last_state: rs.RunState | None = None
        self._loading = False

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(880, 600)
        self.resize(1240, 820)

        self._build()
        self._wire()
        self._load_preferences()
        self.tm.changed.connect(self._theme_changed)
        self._theme_changed(self.tm.palette)
        self.navigate(0)

    # ================================================================ build
    def _build(self):
        root = QWidget()
        root.setObjectName("appRoot")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        # sidebar --------------------------------------------------------
        self.sidebar = QFrame()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(224)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 16, 12, 14)
        side.setSpacing(2)
        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(28, 28)
        brand.addWidget(self.logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        names.addWidget(W.label("Crisp Automation", "brandName"))
        names.addWidget(W.label("Email campaigns", "brandSub"))
        brand.addLayout(names, 1)
        side.addLayout(brand)
        side.addSpacing(22)
        side.addWidget(W.label("WORKSPACE", "sidebarSection"))
        side.addSpacing(4)

        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons = []
        for i, (key, title, icon_name, _) in enumerate(PAGES):
            b = W.button(title, "navItem")
            b.setCheckable(True)
            b.setIconSize(QSize(16, 16))
            b.setToolTip(f"{title}  (Ctrl+{i + 1})")
            b._icon_name = icon_name
            self.nav_group.addButton(b, i)
            self.nav_buttons.append(b)
            side.addWidget(b)
        side.addStretch()

        self.theme_toggle = W.button("", "navItem")
        self.theme_toggle.setIconSize(QSize(16, 16))
        side.addWidget(self.theme_toggle)
        side.addSpacing(6)
        self.version_label = W.label(f"Version {APP_VERSION}", "sidebarFoot", wrap=True)
        side.addWidget(self.version_label)
        h.addWidget(self.sidebar)

        # content ----------------------------------------------------------
        content = QWidget()
        content.setObjectName("appRoot")
        v = QVBoxLayout(content)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)

        self.top = QFrame()
        self.top.setObjectName("topBar")
        self.top.setFixedHeight(64)
        t = QHBoxLayout(self.top)
        t.setContentsMargins(28, 0, 20, 0)
        t.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addStretch()
        self.page_title = W.label("", "pageTitle")
        self.page_subtitle = W.label("", "pageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        titles.addStretch()
        t.addLayout(titles, 1)
        self.status = W.StatusPill(self.tm)
        t.addWidget(self.status)
        self.run_button = W.button("Start dry run", "primary")
        self.run_button.setIconSize(QSize(14, 14))
        self.run_button.setMinimumWidth(132)
        t.addWidget(self.run_button)
        v.addWidget(self.top)

        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(self.tm)
        self.campaign = CampaignPage(self.tm)
        self.activity = ActivityPage(self.tm)
        self.settings = SettingsPage(self.tm)
        self.history = HistoryPage(self.tm)
        for page in (self.dashboard, self.campaign, self.activity, self.settings, self.history):
            self.stack.addWidget(page)
        v.addWidget(self.stack, 1)
        h.addWidget(content, 1)

        self.highlighter = W.LogHighlighter(self.activity.log.document(), self.tm)
        self.csv_timer = QTimer(self)
        self.csv_timer.setSingleShot(True)
        self.csv_timer.setInterval(250)

    # ================================================================ wiring
    def _wire(self):
        self.nav_group.idClicked.connect(self.navigate)
        for i in range(len(PAGES)):
            QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self, activated=lambda i=i: self.navigate(i))
        self.theme_toggle.clicked.connect(self.toggle_theme)
        self.run_button.clicked.connect(self.run_button_clicked)

        d = self.dashboard
        d.open_logs.clicked.connect(lambda: self.navigate(2))
        d.edit_campaign.clicked.connect(lambda: self.navigate(1))

        c = self.campaign
        c.browse.clicked.connect(self.browse_csv)
        c.dnc_browse.clicked.connect(self.browse_dnc)
        c.retry_failed.clicked.connect(lambda: self.start_run(retry_only=True))
        c.random_delay.toggled.connect(lambda on: None if self._loading else self.prefs.set_random_delay(on))

        h = self.history
        h.refresh.clicked.connect(self.refresh_history)
        h.open_run.clicked.connect(self.open_selected_run)
        h.open_runs.clicked.connect(lambda: self._open_folder(self.runs_root))
        h.export_report.clicked.connect(self.export_report)
        h.copy_diag.clicked.connect(self.copy_diagnostics)
        c.skip_failed.toggled.connect(lambda _c: self._update_summary())
        c.reload.clicked.connect(self.refresh_csv)
        c.reset.clicked.connect(self.reset_campaign)
        c.start.clicked.connect(self.start_run)
        c.dry.toggled.connect(self._dry_toggled)
        for key, field in c.fields().items():
            field.edit.textChanged.connect(lambda value, k=key: self._field_changed(k, value))
        self.csv_timer.timeout.connect(self.refresh_csv)

        a = self.activity
        a.stop.clicked.connect(self.stop_run)
        a.clear.clicked.connect(self.clear_log)
        a.copy.clicked.connect(self.copy_log)
        a.save.clicked.connect(self.save_log)

        s = self.settings
        s.theme.changed.connect(self.set_theme)
        s.open_profile.clicked.connect(self.open_profile_folder)
        s.copy_profile.clicked.connect(
            lambda: (QGuiApplication.clipboard().setText(str(self.profile_dir)),
                     self.flash(s.copy_profile, "Copied")))
        s.reset.clicked.connect(self.reset_campaign)
        s.check_updates.clicked.connect(self.check_updates)
        s.check_crisp.clicked.connect(self.check_crisp)
        s.install_update.clicked.connect(self.install_update)
        self.updater.checked.connect(self._update_checked)
        self.updater.check_failed.connect(self._update_status)
        self.updater.progress.connect(self._update_status)
        self.updater.installed.connect(self._update_installed)
        self.updater.install_failed.connect(self._update_install_failed)

        self.runner.line.connect(self._agent_line)
        self.runner.state_changed.connect(self._render_state)
        self.runner.finished.connect(self._run_finished)

    def _load_preferences(self):
        self._loading = True
        values = self.prefs.campaign()
        for key, field in self.campaign.fields().items():
            field.edit.setText(values[key])
        self.campaign.dry.setChecked(True)   # always start safe
        self.campaign.skip_failed.setChecked(False)   # opt-in every launch
        self.campaign.random_delay.setChecked(self.prefs.random_delay())
        sc.delete_old_runs(self.runs_root, days=30)
        self.settings.theme.set_value(self.tm.mode)
        geo = self.prefs.geometry()
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass
        self.settings.profile_path.setText(str(self.profile_dir))
        self.settings.about.set("agent", str(self.runner.script))
        self.settings.about.set("python", sys.executable)
        self.settings.about.set("config", self.prefs._s.fileName())
        self._loading = False
        self.refresh_csv()
        self._update_mode()
        self._render_state(None)
        self._show_crash_recovery()
        self.refresh_history()

    def _show_crash_recovery(self):
        rec = self.prefs.recovery()
        if not rec:
            return
        who = rec.get("email") or "before the first recipient"
        msg = (f"The last run ({rec.get('mode') or 'run'}, started {rec.get('started') or '?'}) did not finish "
               f"normally. It stopped at {who}. Check Crisp before continuing: that email may or may not have "
               f"gone out, and its CSV row may not be marked.")
        for banner in (self.dashboard.unconfirmed_banner, self.activity.unconfirmed_banner):
            banner.set_text(msg)
            banner.show()

    # ============================================================ navigation
    def navigate(self, index: int):
        if not 0 <= index < len(PAGES):
            return
        self.stack.setCurrentIndex(index)
        self.nav_buttons[index].setChecked(True)
        _, title, _, subtitle = PAGES[index]
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        if index == 3:
            exists = self.profile_dir.exists()
            self.settings.profile_state.setText(
                "Profile exists. Your Crisp login will be reused." if exists
                else "Not created yet. It is created the first time the browser opens.")
        if index in (0, 1) and not self.runner.is_running():
            self.refresh_csv()
        if self.stack.widget(index) is self.history:
            self.refresh_history()
        self._refresh_nav_icons()

    # ================================================================ theme
    def set_theme(self, mode: str):
        self.prefs.set_theme(mode)
        self.tm.apply(mode)
        self.settings.theme.set_value(mode)

    def toggle_theme(self):
        self.set_theme("light" if self.tm.palette.name == "dark" else "dark")

    def _theme_changed(self, p: Palette):
        self.logo.setPixmap(logo_pixmap(p.accent))
        for b in self.nav_buttons:
            color = p.text if b.isChecked() else p.text_muted
            b.setIcon(icon(b._icon_name, color))
        dark = p.name == "dark"
        self.theme_toggle.setText("Light mode" if dark else "Dark mode")
        self.theme_toggle.setIcon(icon("sun" if dark else "moon", p.text_muted))
        self._style_run_button()
        for bar in (self.activity.progress, self.dashboard.run_progress):
            bar.style().unpolish(bar)
            bar.style().polish(bar)

    def _refresh_nav_icons(self):
        self._theme_changed(self.tm.palette)

    # ============================================================== campaign
    def _field_changed(self, key, value):
        if self._loading:
            return
        self.prefs.set_campaign_value(key, value.strip())
        if key == "csv":
            # A different file has different rows: forget the old range.
            for f in (self.campaign.row_from, self.campaign.row_to):
                f.edit.clear()
        if key in ("csv", "reference", "row_from", "row_to", "dnc"):
            self.csv_timer.start()
        self._validate_fields()
        self._update_summary()

    def _dry_toggled(self, _checked):
        self._update_mode()
        self._update_summary()

    def dry_run(self) -> bool:
        return self.campaign.dry.isChecked()

    def campaign_values(self) -> dict:
        values = {k: f.text() for k, f in self.campaign.fields().items()}
        values["skip_failed"] = "true" if self.campaign.skip_failed.isChecked() else "false"
        values["random_delay"] = "true" if self.campaign.random_delay.isChecked() else "false"
        return values

    def browse_dnc(self):
        current = self.campaign.dnc.text() or self.campaign.csv.text()
        start = str(Path(current).expanduser().parent) if current else str(Path.home())
        if not Path(start).is_dir():
            start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose do-not-contact list", start,
                                              "CSV or text (*.csv *.txt);;All files (*)")
        if path:
            self.campaign.dnc.edit.setText(path)
            self.refresh_csv()

    def browse_csv(self):
        current = self.campaign.csv.text()
        start = str(Path(current).expanduser().parent) if current else str(Path.home())
        if not Path(start).is_dir():
            start = str(Path.home())
        path, _ = QFileDialog.getOpenFileName(self, "Choose recipient CSV", start,
                                              "CSV files (*.csv);;All files (*)")
        if path:
            self.campaign.csv.edit.setText(path)
            self.refresh_csv()

    def refresh_csv(self):
        self.csv_timer.stop()
        v = self.campaign_values()
        self.stats = read_stats(v["csv"], v["reference"], v["row_from"], v["row_to"], v["dnc"])
        self._validate_fields()
        self._update_metrics()
        self._update_summary()
        self._update_mode()

    def _validate_fields(self) -> bool:
        c = self.campaign
        ok = True
        st = self.stats
        for f in (c.row_from, c.row_to):
            f.set_state("help")
        if st.ok and st.range_error:
            c.csv.set_state("ok", f"{st.total} rows in the file")
            field = c.row_from if st.range_error_field == "from" else c.row_to
            field.set_state("error", st.range_error)
            ok = False
        elif st.ok:
            text = st.preview().replace(", ", " · ")
            if st.invalid_rows:
                shown = "; ".join(f"row {n}: {e or '(blank)'} ({why})" for n, e, why in st.invalid_rows[:3])
                more = f" and {len(st.invalid_rows) - 3} more" if len(st.invalid_rows) > 3 else ""
                text += f"\nInvalid (will be skipped): {shown}{more}"
            c.csv.set_state("ok", text)
        else:
            c.csv.set_state("error", st.error)
            ok = False
        if st.dnc_error:
            c.dnc.set_state("error", st.dnc_error)
            ok = False
        else:
            c.dnc.set_state("help")
        for f in (c.max_skips, c.max_fails_in_row):
            f.set_state("help")
        for f, check in ((c.send_window, sc.parse_window),
                         (c.start_at, lambda t: sc.parse_hhmm(t) if t else None)):
            try:
                check(f.text())
                f.set_state("help")
            except ValueError as exc:
                f.set_state("error", str(exc))
                ok = False
        if c.start_at.text() and ok:
            try:
                when = sc.next_occurrence(c.start_at.text())
                day = "today" if when.date() == sc.now_dhaka().date() else "tomorrow"
                c.start_at.set_state("ok", f"Starts {day} at {when:%H:%M} Dhaka time.")
            except ValueError:
                pass
        c.retry_failed.setEnabled(st.ok and not st.range_error and st.retry_candidates > 0
                                  and not self.runner.is_running())
        c.retry_failed.setText(f"Retry failed only ({st.retry_candidates})" if st.retry_candidates
                               else "Retry failed only")
        for key in ("subject", "shortcut", "segment"):
            f = c.fields()[key]
            if not f.text():
                f.set_state("error", "Required.")
                ok = False
            else:
                f.set_state("help")
        if not c.reference.text():
            c.reference.set_state("help", "Empty: only rows with an empty Reference column are sent.")
        else:
            c.reference.set_state("help")
        return ok

    def reset_campaign(self):
        if self.runner.is_running():
            return
        if not W.ask(self, "Reset campaign fields?",
                     "The CSV path, subject, shortcut, segment and reference go back to their original values. "
                     "Your CSV file itself is not changed.", confirm="Reset"):
            return
        values = self.prefs.reset_campaign()
        self._loading = True
        for key, field in self.campaign.fields().items():
            field.edit.setText(values[key])
        self._loading = False
        self.refresh_csv()

    def _update_mode(self):
        c = self.campaign
        dry = self.dry_run()
        if dry:
            c.mode_banner.hide()
        else:
            n = self.stats.eligible if self.stats.ok else 0
            c.mode_banner.set_text(
                f"Dry Run is off. Starting will send real emails to {n} recipient{'s' if n != 1 else ''}. "
                "You'll be asked to confirm first.")
            c.mode_banner.show()
        self._style_run_button()

    def _style_run_button(self):
        running = self.runner.is_running()
        state = self.runner.state
        p = self.tm.palette
        b = self.run_button
        if running:
            stopping = state is not None and state.phase == rs.STOPPING
            b.setText("Force stop" if stopping else "Stop")
            b.setObjectName("dangerOutline")
            b.setIcon(icon("stop", p.danger))
        else:
            dry = self.dry_run()
            b.setText("Start dry run" if dry else "Start sending")
            b.setObjectName("primary" if dry else "danger")
            b.setIcon(icon("play" if dry else "send", p.accent_text))
        b.style().unpolish(b)
        b.style().polish(b)
        c = self.campaign.start
        c.setText(b.text() if not running else "Running…")
        c.setEnabled(not running)
        c.setObjectName(b.objectName() if not running else "primary")
        c.setIcon(icon("play" if self.dry_run() else "send", p.accent_text))
        c.style().unpolish(c)
        c.style().polish(c)

    # ============================================================ dashboard
    def _update_metrics(self):
        d = self.dashboard
        st = self.stats
        if st.ok:
            ref = self.campaign.reference.text() or "(empty)"
            if st.range_set and not st.range_error:
                d.m_total.set(st.total, f"Using rows {st.range_from}–{st.range_to}")
                d.m_eligible.set(st.eligible, f"Rows {st.range_from}–{st.range_to}, reference “{ref}”")
            else:
                d.m_total.set(st.total, f"{st.marked_sent} marked sent in CSV" if not st.range_error
                              else "Row range is invalid")
                d.m_eligible.set(st.eligible, f"Reference “{ref}”, not yet sent")
            d.csv_banner.hide()
        else:
            d.m_total.set("—", "CSV not loaded")
            d.m_eligible.set("—", "")
            d.csv_banner.set_text(f"Recipient CSV problem: {st.error} Fix it in Campaign Setup.")
            d.csv_banner.show()
        self._update_run_metrics()

    def _update_run_metrics(self):
        """Sent/Failed come only from what the agent reported."""
        d = self.dashboard
        s = self.runner.state or self.last_state
        if s is not None:
            scope = "this run" if self.runner.is_running() else "last run"
            if s.dry_run:
                d.m_sent.set(0, f"Dry run: {s.completed} passed, none sent")
            else:
                d.m_sent.set(s.sent_confirmed, f"Confirmed by Crisp, {scope}")
            d.m_failed.set(s.failed, "Details in Activity Logs" if s.failed or s.last_error else f"In the {scope}")
            self._add_sent7()
            return
        last = self.prefs.last_run()
        if last:
            when = last.get("finished_at", "")
            if last.get("mode") == "dry":
                d.m_sent.set(0, f"Last run ({when}) was a dry run")
            else:
                d.m_sent.set(last.get("sent", 0), f"Confirmed by Crisp, last run {when}")
            d.m_failed.set(last.get("failed", 0), f"Last run {when}")
        else:
            d.m_sent.set(0, "No runs yet")
            d.m_failed.set(0, "No runs yet")
        self._add_sent7()

    def _add_sent7(self):
        if self._sent7:
            m = self.dashboard.m_sent
            current = m.caption.text()
            note = f"{self._sent7} in the last 7 days"
            if note not in current:
                text = f"{current} · {note}" if current else note
                m.caption.setText(text)
                m.caption.setToolTip(text)

    def _update_summary(self):
        kv = self.dashboard.kv
        v = self.campaign_values()
        csv_name = Path(v["csv"]).name if v["csv"] else ""
        kv.set("csv", csv_name, v["csv"])
        kv.set("subject", v["subject"])
        kv.set("shortcut", v["shortcut"])
        kv.set("segment", v["segment"])
        kv.set("reference", v["reference"] or "(empty)")
        kv.set("mode", "Dry Run, no emails sent" if self.dry_run() else "Real sending")
        last = self.prefs.last_run()
        if last:
            mode = "Dry run" if last.get("mode") == "dry" else "Real run"
            outcome = str(last.get("outcome", "")).capitalize()
            self.dashboard.last_run.setText(
                f"Last run: {mode}, {outcome.lower()} at {last.get('finished_at')}. "
                f"{last.get('completed')} completed, {last.get('failed')} failed.")
        else:
            self.dashboard.last_run.setText("No previous runs on this computer.")

    # ================================================================== run
    def run_button_clicked(self):
        if self.runner.is_running():
            self.stop_run()
        else:
            self.start_run()

    def start_run(self, retry_only: bool = False):
        retry_only = retry_only is True   # clicked(bool) passes False here
        if self.runner.is_running() or self.updater.busy:
            return
        self.refresh_csv()
        if not self._validate_fields():
            self.navigate(1)
            problem = (self.stats.error if not self.stats.ok
                       else self.stats.range_error or self.stats.dnc_error or "Fix the fields marked in red.")
            W.inform(self, "Check the campaign settings", problem)
            return
        if retry_only and self.stats.retry_candidates == 0:
            W.inform(self, "Nothing to retry", "No rows have Status Failed or Skipped (in the selected rows).")
            return
        if not retry_only and self.stats.eligible == 0:
            self.navigate(1)
            W.inform(self, "No eligible recipients",
                     "No rows match the reference filter with an email and Mail Sent unchecked. "
                     "Nothing would be sent.",
                     details=[("CSV rows", str(self.stats.total)),
                              ("Already sent", str(self.stats.marked_sent)),
                              ("Other reference", str(self.stats.other_reference))])
            return
        v = self.campaign_values()
        dry = self.dry_run()
        n = self.stats.retry_candidates if retry_only else self.stats.eligible
        if not dry:
            ok = W.ask(
                self, f"Send {n} real email{'s' if n != 1 else ''}?",
                "Dry Run is off. The agent will send real emails through Crisp and mark each sent row in the CSV. "
                "This can't be undone.",
                details=[("Recipients", str(n) + (" (retry failed only)" if retry_only else "")),
                         ("Limits", self._limits_text(v)),
                         ("Rows", f"{self.stats.range_from}–{self.stats.range_to} of {self.stats.total}"),
                         ("Subject", v["subject"]), ("Shortcut", v["shortcut"]),
                         ("Segment", v["segment"]), ("CSV", Path(v["csv"]).name)],
                confirm=f"Send {n} email{'s' if n != 1 else ''}", tone="danger")
            if not ok:
                return
        v["retry_only"] = "true" if retry_only else "false"
        v["history_file"] = str(self.history_file)
        try:
            self.run_dir = sc.new_run_dir(self.runs_root, dry)
            v["run_dir"] = str(self.run_dir)
            settings = {k: val for k, val in v.items()}
            settings.update({"dry_run": dry, "app_version": APP_VERSION, "started": sc.stamp()})
            (self.run_dir / "settings.json").write_text(json.dumps(settings, indent=2, ensure_ascii=False),
                                                         encoding="utf-8")
            self._run_log = (self.run_dir / "run.log").open("a", encoding="utf-8")
        except OSError as exc:
            self.run_dir, self._run_log = None, None
            v["run_dir"] = ""
            self._gui_line(f"Could not create the run folder ({exc}); continuing without it.")
        err = self.runner.start(v, dry, self.profile_dir)
        if err:
            self._close_run_log()
            W.inform(self, "Could not start", err)
            return
        self.last_state = None
        self.activity.error_banner.hide()
        self.activity.unconfirmed_banner.hide()
        self.activity.retry_banner.hide()
        self.dashboard.unconfirmed_banner.hide()
        self.prefs.set_recovery(email="", csv=v["csv"], started=f"{datetime.now():%Y-%m-%d %H:%M}",
                                mode="dry run" if dry else "real sending")
        self._recovery_email = ""
        self._gui_line(f"Run started {datetime.now():%Y-%m-%d %H:%M:%S} "
                       f"({'DRY RUN, nothing will be sent' if dry else 'REAL SENDING'})")
        self._set_inputs_locked(True)
        self.navigate(2)
        self._render_state(self.runner.state)

    def stop_run(self):
        if not self.runner.is_running():
            return
        state = self.runner.state
        if state and state.phase == rs.STOPPING:
            if W.ask(self, "Force stop now?",
                     "The agent is finishing the current recipient. Forcing it to stop now can interrupt a send "
                     "after it was clicked, before the CSV is marked. Check that conversation in Crisp before "
                     "running again.", confirm="Force stop", tone="danger"):
                self.runner.force_stop()
                self._gui_line("Force stop requested.")
            return
        if self.runner.can_stop_gracefully():
            if not W.ask(self, "Stop the run?",
                         "The agent will finish the current recipient, then stop before the next one. "
                         "Nothing is cut off halfway.", confirm="Stop after this recipient"):
                return
            result = self.runner.request_stop()
        else:
            if not W.ask(self, "Stop the run?",
                         "No email is being processed yet, so the agent will stop immediately.",
                         confirm="Stop"):
                return
            result = self.runner.request_stop()
        self._gui_line("Stopping after the current recipient…" if result == "graceful" else "Stopping…")
        self._render_state(self.runner.state)

    def _set_inputs_locked(self, locked: bool):
        c = self.campaign
        for f in c.fields().values():
            f.edit.setEnabled(not locked)
        for w in (c.browse, c.reload, c.reset, c.dry, self.settings.reset):
            w.setEnabled(not locked)
        c.locked_banner.setVisible(locked)

    # ================================================================ log
    def _gui_line(self, text: str):
        self._append(f"› {text}")

    def _agent_line(self, text: str):
        if text.startswith(rs.EVENT_PREFIX):
            # Machine-readable; kept in run.log, hidden from the on-screen log.
            if self._run_log:
                try:
                    self._run_log.write(text + "\n")
                except OSError:
                    pass
            return
        self._append(text)
        if self._run_log:
            try:
                self._run_log.write(text + "\n")
                self._run_log.flush()
            except OSError:
                pass
        stripped = text.strip()
        if stripped in ("Mail Sent marked TRUE.",):
            self.refresh_csv()

    def _append(self, text: str):
        log = self.activity.log
        bar = log.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        log.appendPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def clear_log(self):
        self.activity.log.clear()

    def copy_log(self):
        QGuiApplication.clipboard().setText(self.activity.log.toPlainText())
        self.flash(self.activity.copy, "Copied")

    def save_log(self):
        default = str(Path.home() / f"crisp-run-{datetime.now():%Y%m%d-%H%M%S}.log")
        path, _ = QFileDialog.getSaveFileName(self, "Save log", default, "Log files (*.log *.txt);;All files (*)")
        if not path:
            return
        try:
            Path(path).write_text(self.activity.log.toPlainText(), encoding="utf-8")
        except OSError as exc:
            W.inform(self, "Could not save the log", str(exc))
            return
        self.flash(self.activity.save, "Saved")

    def flash(self, btn, text: str):
        original = btn.text()
        if original == text:
            return
        btn.setText(text)
        QTimer.singleShot(1200, lambda: btn.setText(original))

    # ============================================================ state view
    def _render_state(self, s: rs.RunState | None):
        s = s or self.runner.state or self.last_state
        a, d = self.activity, self.dashboard
        phase = s.phase if s else rs.IDLE
        text, tone = STATUS[phase]
        if phase == rs.RUNNING and s and s.dry_run:
            text = "Dry run"
        self.status.set(text, tone)
        self._style_run_button()
        a.stop.setEnabled(self.runner.is_running())
        a.stop.setText("Force stop" if phase == rs.STOPPING else "Stop")

        if s is None:
            a.step.setText("Not running")
            a.recipient.setText("Recipient status appears here once a run starts.")
            d.run_step.setText("No run yet")
            d.run_detail.setText("Start a dry run to check the campaign end to end.")
            for bar in (a.progress, d.run_progress):
                bar.setValue(0)
            a.percent.setText("0%")
            d.run_progress_text.setText("")
            a.s_done.value.setText("0")
            a.s_failed.value.setText("0")
            a.s_remaining.value.setText("—")
            self._update_run_metrics()
            return

        step = s.step or text
        if phase == rs.FINISHED:
            step = "No eligible recipients" if s.no_eligible else (
                "Dry run finished, nothing was sent" if s.dry_run else "All emails sent")
        elif phase == rs.FAILED:
            step = "Run failed"
        elif phase == rs.STOPPED:
            step = "Run stopped"
        elif phase == rs.STOPPING:
            step = f"Stopping after this recipient · {s.step}" if s.position else "Stopping…"
        a.step.setText(step)
        d.run_step.setText(step)

        if s.position and s.eligible:
            who = s.current_email + (f" ({s.current_name})" if s.current_name else "")
            detail = f"Recipient {s.position} of {s.eligible}: {who}"
        elif phase == rs.WAITING_LOGIN:
            detail = "Sign in to Crisp in the Chromium window. The run continues automatically."
        elif phase == rs.STARTING:
            detail = "Launching the agent and browser…"
        else:
            detail = f"{s.eligible} eligible recipient{'s' if s.eligible != 1 else ''}" if s.eligible else ""
        a.recipient.setText(detail)
        d.run_detail.setText(detail or ("Dry run" if s.dry_run else "Real sending"))

        value = int(round(s.progress * 1000))
        state_prop = "failed" if phase == rs.FAILED else "done" if phase == rs.FINISHED else ""
        for bar in (a.progress, d.run_progress):
            bar.setValue(value)
            if bar.property("state") != state_prop:
                bar.setProperty("state", state_prop)
                bar.style().unpolish(bar)
                bar.style().polish(bar)
        pct = f"{int(s.progress * 100)}%"
        a.percent.setText(pct)
        d.run_progress_text.setText(
            f"{pct} · {s.processed} of {s.eligible} processed" if s.eligible else pct)

        a.s_done.title.setText("Passed (dry run)" if s.dry_run else "Sent")
        a.s_done.value.setText(str(s.completed if s.dry_run else s.sent_confirmed))
        a.s_failed.value.setText(str(s.failed))
        remaining = max(0, s.eligible - s.processed) if s.eligible else 0
        a.s_remaining.value.setText(str(remaining) if s.eligible else "—")

        if s.last_error and phase in (rs.FAILED, rs.RUNNING, rs.STOPPING):
            a.error_banner.set_text(s.last_error)
            a.error_banner.show()
        if self.runner.is_running() and s.current_email and s.current_email != getattr(self, "_recovery_email", ""):
            self._recovery_email = s.current_email
            self.prefs.set_recovery(email=s.current_email)
        if s.retry and not self.runner.is_running():
            lines = [f"{e} ({why})" if why else e for e, _n, why in s.retry[:5]]
            more = f" and {len(s.retry) - 5} more" if len(s.retry) > 5 else ""
            a.retry_banner.set_text(f"Not sent in this run, retry later: {'; '.join(lines)}{more}. "
                                    "The full list is at the end of the log.")
            a.retry_banner.show()
        if s.unconfirmed_send:
            msg = (f"Send was clicked for {s.current_email or 'the last recipient'}, but Crisp did not confirm it. "
                   "The email may already have gone out and the CSV row was NOT marked. "
                   "Check that conversation in Crisp and mark the row before running again.")
            for banner in (a.unconfirmed_banner, d.unconfirmed_banner):
                banner.set_text(msg)
                banner.show()
        self._update_run_metrics()

    def check_crisp(self):
        """Health check: opens Crisp, never creates or sends anything."""
        if self.runner.is_running() or self.updater.busy:
            return
        v = self.campaign_values()
        v.update({"health_check": "true", "run_dir": "", "history_file": ""})
        err = self.runner.start(v, True, self.profile_dir)
        if err:
            W.inform(self, "Could not start the check", err)
            return
        self._health = True
        self.settings.check_crisp.setEnabled(False)
        self.settings.health_result.setText("Checking… sign in to Crisp in the Chromium window if asked, "
                                            "then open any existing conversation.")
        self._gui_line("Crisp health check started (nothing is created or sent).")
        self._render_state(self.runner.state)

    def _run_finished(self, s: rs.RunState):
        if getattr(self, "_health", False):
            self._health = False
            self.last_state = s
            lines = [f"{'✓' if ok else '✗'} {name}: {detail}" for name, ok, detail in s.health]
            if not lines:
                lines = [s.last_error or "The check stopped before it finished."]
            self.settings.health_result.setText("\n".join(lines))
            self.settings.check_crisp.setEnabled(True)
            self._gui_line("Crisp health check finished.")
            self._render_state(s)
            return
        self.last_state = s
        self.prefs.clear_recovery()
        self._recovery_email = ""
        self._set_inputs_locked(False)
        text, _ = STATUS[s.phase]
        summary = (f"Run {text.lower()}. Completed {s.completed}, failed {s.failed}"
                   + (f", skipped {s.skipped}" if s.skipped else "")
                   + ("" if s.dry_run else f", sent and confirmed {s.sent_confirmed}") + ".")
        self._gui_line(summary)
        self.prefs.set_last_run({
            "finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": "dry" if s.dry_run else "real",
            "outcome": text.lower(),
            "sent": s.sent_confirmed,
            "completed": s.completed,
            "failed": s.failed,
            "eligible": s.eligible,
            "error": s.last_error,
        })
        self.prefs.sync()
        self._close_run_log()
        if self.run_dir:
            summary = {"phase": s.phase, "outcome": text, "mode": "dry" if s.dry_run else "live",
                       "sent": s.sent_confirmed, "completed": s.completed, "failed": s.failed,
                       "skipped": s.skipped, "eligible": s.eligible, "error": s.last_error,
                       "finished": sc.stamp()}
            try:
                (self.run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
            except OSError:
                pass
        self._sent7 = sc.sent_last_days(self.history_file, 7)
        self.refresh_csv()
        self._render_state(s)
        self.refresh_history()
        QApplication.alert(self)

    def _close_run_log(self):
        if self._run_log:
            try:
                self._run_log.close()
            except OSError:
                pass
        self._run_log = None

    def _limits_text(self, v: dict) -> str:
        parts = []
        if v.get("max_per_run"):
            parts.append(f"max {v['max_per_run']} per run")
        if v.get("daily_cap"):
            parts.append(f"daily cap {v['daily_cap']}")
        if v.get("send_window"):
            parts.append(f"window {v['send_window']}")
        if v.get("start_at"):
            parts.append(f"starts {v['start_at']}")
        parts.append("random 20–60 s delay" if v.get("random_delay") == "true" else f"{v.get('delay') or 3} s delay")
        return ", ".join(parts) + " (Dhaka time)" if (v.get("send_window") or v.get("start_at")) else ", ".join(parts)

    # =============================================================== history
    def _run_dirs(self) -> list[Path]:
        if not self.runs_root.is_dir():
            return []
        return sorted((p for p in self.runs_root.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)

    def refresh_history(self):
        h = self.history
        self._sent7 = sc.sent_last_days(self.history_file, 7)
        h.sent7.setText(f"Real emails sent in the last 7 days: {self._sent7} (from history.csv).")
        dirs = self._run_dirs()
        h.table.setRowCount(len(dirs))
        for r, path in enumerate(dirs):
            try:
                info = json.loads((path / "summary.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                info = {"outcome": "Running" if path == self.run_dir and self.runner.is_running() else "Unfinished"}
            started = path.name[:17].replace("_", " ")
            started = f"{started[:13]}:{started[13:15]}" if len(started) >= 15 else started
            mode = info.get("mode") or ("dry" if path.name.endswith("_dry") else "live")
            values = (started, "Dry run" if mode == "dry" else "Real", str(info.get("outcome", "")),
                      str(info.get("sent", "")), str(info.get("failed", "")), str(info.get("skipped", "")))
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.UserRole, str(path))
                h.table.setItem(r, c, item)
        h.empty.setVisible(not dirs)
        if dirs and h.table.currentRow() < 0:
            h.table.selectRow(0)
        self._update_run_metrics()

    def _selected_run(self) -> Path | None:
        item = self.history.table.item(self.history.table.currentRow(), 0)
        return Path(item.data(Qt.UserRole)) if item else None

    def _open_folder(self, path: Path):
        Path(path).mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def open_selected_run(self):
        path = self._selected_run()
        if path:
            self._open_folder(path)

    def export_report(self):
        path = self._selected_run()
        report = path / "report.csv" if path else None
        if not report or not report.exists():
            W.inform(self, "No report for this run", "A report is written when a run gets past the login step.")
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export run report", str(Path.home() / f"report-{path.name}.csv"),
                                                "CSV files (*.csv)")
        if target:
            shutil.copyfile(report, target)
            self.flash(self.history.export_report, "Exported")

    def copy_diagnostics(self):
        settings = self.campaign_values()
        try:
            path = build_zip(self.data_dir / "diagnostics", ROOT, self.activity.log.toPlainText(), settings,
                             self.profile_dir, self.history_file)
        except OSError as exc:
            W.inform(self, "Could not create diagnostics", str(exc))
            return
        QGuiApplication.clipboard().setText(str(path))
        self.history.diag_status.setText(f"Saved and path copied: {path}")
        self.flash(self.history.copy_diag, "Copied")

    # ============================================================== settings
    def open_profile_folder(self):
        path = self.profile_dir
        if not path.exists():
            W.inform(self, "Profile not created yet",
                     "The browser profile folder is created the first time the agent opens Chromium.",
                     details=[("Location", str(path))])
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    # =============================================================== updates
    def check_updates_on_start(self):
        """Called by gui_app.py only, so tests never touch the network."""
        QTimer.singleShot(2000, self.check_updates)

    def check_updates(self):
        if self.updater.check():
            self._update_status("Checking for updates…")
            self.settings.check_updates.setEnabled(False)

    def _update_status(self, text: str):
        self.settings.update_status.setText(text)
        self.settings.check_updates.setEnabled(not self.updater.busy)

    def _update_checked(self, release, text: str):
        self._update_status(text)
        self.settings.install_update.setVisible(release is not None)
        foot = f"Version {APP_VERSION}"
        self.version_label.setText(f"{foot} · v{release.version} available" if release else foot)

    def install_update(self):
        rel = self.updater.release
        if rel is None or self.updater.busy:
            return
        if self.runner.is_running():
            W.inform(self, "Stop the run first",
                     "Updating restarts the app. Wait for the run to finish (or stop it), then update.")
            return
        if not W.ask(self, f"Update to version {rel.version}?",
                     "The app downloads the new version, checks it, backs up the current code, then restarts. "
                     "Your settings, CSV files, history, Crisp login and logs are not touched. "
                     "If anything fails, the current version is restored automatically.",
                     details=[("Current", APP_VERSION), ("New", rel.version), ("Backup", f"backups/{APP_VERSION}")],
                     confirm="Update and restart"):
            return
        if self.updater.install():
            self.settings.install_update.setEnabled(False)
            self.campaign.start.setEnabled(False)
            self.run_button.setEnabled(False)
            self._update_status(f"Installing version {rel.version}…")

    def _update_install_failed(self, text: str):
        self._update_status("Update failed. Your current version still works.")
        self.settings.install_update.setEnabled(True)
        self.campaign.start.setEnabled(True)
        self.run_button.setEnabled(True)
        W.inform(self, "Update failed", text)

    def _update_installed(self, backup: str):
        self._update_status("Update installed. Restarting…")
        res = QProcess.startDetached(sys.executable, [str(ROOT / "gui_app.py")], str(ROOT))
        started = res[0] if isinstance(res, tuple) else bool(res)
        if not started:
            W.inform(self, "Update installed", "Close the app and open it again to use the new version.",
                     details=[("Old code backup", backup)])
            return
        self.close()

    # ================================================================ close
    def closeEvent(self, event):
        if self.runner.is_running():
            if not W.ask(self, "Quit while the agent is running?",
                         "Quitting stops the agent immediately. If an email is being sent right now, the CSV row "
                         "might not get marked. Check the last conversation in Crisp before running again.",
                         confirm="Stop and quit", tone="danger"):
                event.ignore()
                return
            self.runner.force_stop()
            self.runner.proc.waitForFinished(5000)
        self.prefs.set_geometry(self.saveGeometry())
        self.prefs.sync()
        event.accept()

"""Main window: sidebar navigation, top bar, pages, and all behaviour."""

from __future__ import annotations

import json
import shutil
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import send_control as sc  # noqa: E402
import sheet_source as ss  # noqa: E402  (gspread is imported only when used)
import campaign_results as cr  # noqa: E402

from PySide6.QtCore import QObject, QProcess, QSize, Qt, QTimer, QUrl, Signal
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
from .preview_dialog import preview_rows, show_preview
from . import campaign_store as cs
from .pages import ActivityPage, CampaignPage, CampaignsPage, DashboardPage, HistoryPage, ResultsPage, SettingsPage
from .queue_controller import QueueController
from .theme import Palette, ThemeManager
from .update_controller import UpdateController

class _SheetSignal(QObject):
    """Carries a background Google result back to the window's thread."""
    done = Signal(str, object, object)   # kind, result, error message


PAGES = (
    ("dashboard", "Dashboard", "dashboard", "Overview of your campaign and the latest run."),
    ("campaign", "Campaign Setup", "campaign", "Recipients, message and sending mode."),
    ("activity", "Activity Logs", "logs", "Live progress and output from the automation."),
    ("settings", "Settings", "settings", "Appearance, browser session and defaults."),
    ("history", "History", "file", "Past runs, reports and diagnostics."),
    ("results", "Campaign Results", "send", "Sent, delivered, read and replied, read from Crisp (read-only)."),
    ("campaigns", "Campaigns", "play", "Saved campaigns and the run queue."),
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
        self.results = cr.ResultsStore(self.data_dir / "campaign_results.json")
        self._results_check = False
        self._results_campaign = ""
        self.run_dir: Path | None = None
        self._sheet_busy = False
        self._sheet_signal = _SheetSignal(self)
        self._run_log = None
        self._sent7 = 0
        self.prefs = prefs or Preferences()
        self.profile_dir = Path(profile_dir or PROFILE_DIR)
        self.runner = AgentRunner(agent_script or AGENT_SCRIPT, self)
        self.campaign_store = cs.CampaignStore(self.data_dir / "campaigns.json")
        self.queue_ctl = QueueController(self.campaign_store, self.launch_campaign, self.runner,
                                         agent_script or AGENT_SCRIPT, self.prefs_parallel, self)
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
        self.results_page = ResultsPage(self.tm)
        self.campaigns_page = CampaignsPage(self.tm)
        for page in (self.dashboard, self.campaign, self.activity, self.settings, self.history, self.results_page,
                     self.campaigns_page):
            self.stack.addWidget(page)
        v.addWidget(self.stack, 1)
        h.addWidget(content, 1)

        self.highlighter = W.LogHighlighter(self.activity.log.document(), self.tm)
        self.csv_timer = QTimer(self)
        self.csv_timer.setSingleShot(True)
        self.csv_timer.setInterval(250)
        self.eta_timer = QTimer(self)          # keeps the ETA ticking during a run
        self.eta_timer.setInterval(2000)
        self.eta_timer.timeout.connect(lambda: self._render_eta(self.runner.state))

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
        c.source.changed.connect(self._source_changed)
        c.key_browse.clicked.connect(self.browse_key)
        c.load_tabs.clicked.connect(self.load_sheet_tabs)
        c.test_sheet.clicked.connect(self.test_sheet)
        self._sheet_signal.done.connect(self._sheet_job_done)
        c.sheet_tab.currentTextChanged.connect(self._sheet_tab_changed)
        c.retry_failed.clicked.connect(lambda: self.start_run(retry_only=True))
        c.preview.clicked.connect(self.show_row_preview)
        c.random_delay.toggled.connect(lambda on: None if self._loading else self.prefs.set_random_delay(on))

        h = self.history
        h.refresh.clicked.connect(self.refresh_history)
        h.open_run.clicked.connect(self.open_selected_run)
        h.open_runs.clicked.connect(lambda: self._open_folder(self.runs_root))
        h.export_report.clicked.connect(self.export_report)
        h.copy_diag.clicked.connect(self.copy_diagnostics)

        r = self.results_page
        r.campaign.currentIndexChanged.connect(lambda _i: self.refresh_results())
        for combo in (r.f_status, r.f_version, r.f_date):
            combo.currentIndexChanged.connect(lambda _i: self._fill_results_table())
        r.refresh.clicked.connect(self.refresh_statuses)
        r.stop.clicked.connect(self._stop_status_check)
        r.export.clicked.connect(self.export_results)
        r.add_dnc.clicked.connect(self.add_failed_to_dnc)
        for t in (r.table, r.followup, r.problems):
            t.cellClicked.connect(lambda row, col, t=t: self._open_link_cell(t, row, col))
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
        a.pause.clicked.connect(self.toggle_pause)
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

        cp = self.campaigns_page
        cp.save_new.clicked.connect(self.save_campaign_new)
        cp.update_sel.clicked.connect(self.update_campaign)
        cp.open_sel.clicked.connect(self.open_campaign)
        cp.duplicate.clicked.connect(lambda: self._campaign_action(self.campaign_store.duplicate))
        cp.delete.clicked.connect(self.delete_campaign)
        cp.enqueue.clicked.connect(lambda: self._campaign_action(self.campaign_store.enqueue))
        cp.dequeue.clicked.connect(lambda: self._campaign_action(self.campaign_store.dequeue))
        cp.up.clicked.connect(lambda: self._campaign_action(lambda cid: self.campaign_store.move(cid, -1)))
        cp.down.clicked.connect(lambda: self._campaign_action(lambda cid: self.campaign_store.move(cid, 1)))
        cp.run_next.clicked.connect(lambda: self._campaign_action(self.campaign_store.run_next))
        cp.pause.clicked.connect(self.pause_campaign)
        cp.stop.clicked.connect(self.stop_campaign)
        cp.checked.clicked.connect(lambda: self._campaign_action(self.campaign_store.mark_checked))
        cp.run_queue.clicked.connect(self.run_queue)
        cp.stop_queue.clicked.connect(self.queue_ctl.stop_queue)
        cp.table.itemSelectionChanged.connect(self._update_campaign_buttons)
        self.queue_ctl.changed.connect(self.refresh_campaigns)
        self.queue_ctl.message.connect(self._gui_line)
        self.queue_ctl.halted.connect(lambda text: W.inform(self, "Queue stopped", text))
        self.runner.state_changed.connect(lambda _s: self._queue_progress())
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
        tab = self.prefs.sheet_tab()
        if tab:
            self.campaign.sheet_tab.addItem(tab)
        self._apply_source(self.prefs.source())
        sc.delete_old_runs(self.runs_root, days=30)
        self.settings.theme.set_value(self.tm.mode)
        shared = self.prefs.shared()
        for key, field in (("runner_name", self.settings.runner_name), ("claim_batch", self.settings.claim_batch),
                           ("claim_expiry", self.settings.claim_expiry)):
            field.edit.setText(shared[key])
            field.edit.editingFinished.connect(self._save_shared)
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
        self.settings.parallel.edit.setText(str(self.prefs.parallel()))
        self.settings.parallel.edit.editingFinished.connect(self._save_parallel)
        notes = self.campaign_store.restore()
        for n in notes:
            self._gui_line(n)
        self._restore_notes = notes
        self.refresh_campaigns()

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
        if self.stack.widget(index) is self.results_page:
            self.refresh_results()
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
        if key in ("sheet_url",):
            self._sheet_preview_key = None
        if key in ("csv", "reference", "row_from", "row_to", "dnc", "sheet_url"):
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
        values["source"] = self.source()
        values["sheet_tab"] = self.campaign.sheet_tab.currentText().strip()
        values["data_dir"] = str(self.data_dir)
        values.update(self._shared_values())
        return values

    @staticmethod
    def _shortcut_text(v: dict) -> str:
        try:
            variants = sc.parse_variants(v.get("shortcut", ""), v.get("shortcut_version", ""))
        except ValueError:
            return f"Shortcut: {v.get('shortcut', '')}"
        if len(variants) == 1:
            return f"Shortcut: {variants[0][0]}"
        return "A/B test, split evenly: " + " / ".join(f"{n} ({lab})" for n, lab in variants)

    def _shared_values(self) -> dict:
        s = self.settings
        out = {"runner_name": s.runner_name.text()}
        for key, field, default, low, high in (("claim_batch", s.claim_batch, 10, 1, 100),
                                               ("claim_expiry", s.claim_expiry, 5, 2, 120)):
            try:
                value = min(high, max(low, int(field.text())))
            except ValueError:
                value = default
            out[key] = str(value)
        return out

    def _save_shared(self):
        for key, value in self._shared_values().items():
            self.prefs.set_shared(key, value)

    # ============================================================ Google Sheet
    def source(self) -> str:
        return self.campaign.source.value() or "csv"

    def _apply_source(self, value: str):
        c = self.campaign
        c.source.set_value(value)
        c.csv_box.setVisible(value == "csv")
        c.sheet_box.setVisible(value == "sheet")

    def _source_changed(self, value: str):
        self.prefs.set_source(value)
        self._apply_source(value)
        self.refresh_csv()

    def _sheet_tab_changed(self, value: str):
        if not self._loading:
            self.prefs.set_sheet_tab(value.strip())
            self.refresh_csv()

    def browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose the service-account key", str(Path.home()),
                                              "JSON key (*.json);;All files (*)")
        if path:
            self.campaign.google_key.edit.setText(path)

    def _sheet_factory(self, url: str, tab: str, key: str):
        return ss.SheetSource(url, tab, key).connect()

    def _sheet_inputs(self):
        """(url, key, tab) if the link and key look right, else None (errors shown on the fields)."""
        c = self.campaign
        problem = ss.key_problem(c.google_key.text(), ROOT)
        if problem:
            c.google_key.set_state("error", problem)
            c.sheet_info.setText(problem)
            return None
        try:
            ss.sheet_id(c.sheet_url.text())
        except ss.SheetError as exc:
            c.sheet_url.set_state("error", str(exc))
            c.sheet_info.setText(str(exc))
            return None
        return c.sheet_url.text(), c.google_key.text(), c.sheet_tab.currentText().strip()

    def _run_sheet_job(self, kind: str, job) -> bool:
        """Google calls run in a background thread so the window never freezes."""
        if self._sheet_busy:
            return False
        self._sheet_busy = True
        c = self.campaign
        for b in (c.load_tabs, c.test_sheet):
            b.setEnabled(False)
        c.test_sheet.setText("Connecting…")
        c.sheet_info.setText("Connecting to Google… (this can take up to 30 seconds)")

        def work():
            try:
                result, error = job(), None
            except ss.SheetError as exc:
                result, error = None, str(exc)
            except Exception as exc:  # noqa: BLE001
                result, error = None, ss.describe(exc)
            self._sheet_signal.done.emit(kind, result, error)
        threading.Thread(target=work, daemon=True).start()
        return True

    def _sheet_job_done(self, kind: str, result, error):
        self._sheet_busy = False
        c = self.campaign
        for b in (c.load_tabs, c.test_sheet):
            b.setEnabled(True)
        c.test_sheet.setText("Test connection")
        if error:
            c.sheet_info.setText(error)
            return
        tabs, tab, sheet, rows = result
        combo = c.sheet_tab
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(tabs)
        if tab in tabs:
            combo.setCurrentText(tab)
        combo.blockSignals(False)
        self._sheet_tab_changed(combo.currentText())
        if kind == "tabs":
            c.sheet_info.setText(f"{len(tabs)} tab(s) found. Choose one, then click Test connection.")
            return
        self._sheet_preview_key = (c.sheet_url.text(), tab)
        pending = ss.PendingQueue(self.data_dir / "sheet_cache" / "pending.json").for_sheet(sheet, tab)
        self.refresh_csv()
        note = f" {len(pending)} earlier result(s) still waiting to be written to the Sheet." if pending else ""
        c.sheet_info.setText(
            f"Connected as {ss.service_account_email(c.google_key.text())}. "
            f"Tab '{tab}': {self.stats.preview() if self.stats.ok else self.stats.error}.{note}")

    def load_sheet_tabs(self):
        inputs = self._sheet_inputs()
        if inputs is None:
            return
        url, key, tab = inputs

        def job():
            tabs = self._sheet_factory(url, "", key).tabs()
            return tabs, tab, "", 0
        self._run_sheet_job("tabs", job)

    def _sheet_preview_path(self) -> Path:
        return self.data_dir / "sheet_cache" / "preview.csv"

    def _sheet_shared_path(self) -> Path:
        return self.data_dir / "sheet_cache" / "preview_shared.json"

    def test_sheet(self):
        """Connect, list tabs (pick the first if none chosen), download, show
        '120 rows, 45 eligible, 3 duplicates'. Runs in the background."""
        inputs = self._sheet_inputs()
        if inputs is None:
            return
        url, key, tab = inputs
        preview = self._sheet_preview_path()

        def job():
            src = self._sheet_factory(url, tab, key)
            tabs = src.tabs()
            chosen = tab if tab in tabs else (tabs[0] if tabs else "")
            if not chosen:
                raise ss.SheetError("The Sheet has no tabs.")
            src.tab, src.ws = chosen, None
            rows, header = src.download()
            ss.SheetSource.write_local(preview, rows, header)
            import claims
            shared = self._sheet_shared_path()
            shared.write_text(json.dumps(claims.read_raw(src)), encoding="utf-8")
            return tabs, chosen, src.id, len(rows)
        self._run_sheet_job("test", job)

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
        path = v["csv"]
        if self.source() == "sheet":
            fresh = getattr(self, "_sheet_preview_key", None) == (v["sheet_url"], v["sheet_tab"])
            path = str(self._sheet_preview_path()) if fresh and self._sheet_preview_path().exists() else ""
        shared = str(self._sheet_shared_path()) if self.source() == "sheet" and path else ""
        self.stats = read_stats(path, v["reference"], v["row_from"], v["row_to"], v["dnc"],
                                history_file=str(self.history_file), campaign=self.campaign_name(v),
                                recent_days=v.get("recent_days") or 0, shared_file=shared,
                                claim_expiry=v.get("claim_expiry") or 5)
        if self.source() == "sheet" and not path:
            self.stats.error = "Click Test connection to read the Sheet."
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
        sheet = self.source() == "sheet"
        target = c.sheet_url if sheet else c.csv
        if sheet:
            c.csv.set_state("help")
            problem = ss.key_problem(c.google_key.text(), ROOT)
            c.google_key.set_state("error", problem) if problem else c.google_key.set_state("help")
            ok = ok and not problem
        if st.ok and st.range_error:
            target.set_state("ok", f"{st.total} rows in the file")
            field = c.row_from if st.range_error_field == "from" else c.row_to
            field.set_state("error", st.range_error)
            ok = False
        elif st.ok:
            text = st.preview()
            if st.recent or st.campaign_sent:
                text += "\nSee who and when: Preview rows…"
            if st.invalid_rows:
                shown = "; ".join(f"row {n}: {e or '(blank)'} ({why})" for n, e, why in st.invalid_rows[:3])
                more = f" and {len(st.invalid_rows) - 3} more" if len(st.invalid_rows) > 3 else ""
                text += f"\nInvalid (will be skipped): {shown}{more}"
            target.set_state("ok", text)
        else:
            target.set_state("error", st.error)
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
        if c.shortcut.text():
            try:
                variants = sc.parse_variants(c.shortcut.text(), c.shortcut_version.text())
                c.shortcut_version.set_state("help")
                if len(variants) > 1:
                    c.shortcut.set_state("ok", f"A/B test: {len(variants)} shortcuts, split evenly: "
                                         + " · ".join(f"{n} ({lab})" for n, lab in variants))
            except ValueError as exc:
                target = c.shortcut_version if "label" in str(exc) else c.shortcut
                target.set_state("error", str(exc))
                ok = False
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
        if v.get("source") == "sheet":
            kv.set("csv", f"Google Sheet · {v.get('sheet_tab') or 'no tab'}", v.get("sheet_url", ""))
        else:
            kv.set("csv", csv_name, v["csv"])
        kv.set("subject", v["subject"])
        kv.set("shortcut", v["shortcut"] + (f"  ·  {v['shortcut_version']}" if v.get("shortcut_version") else ""))
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
            # Before going live: the per-row table of exactly what will happen.
            source = (f"Google Sheet, tab {v['sheet_tab']}" if v.get("source") == "sheet"
                      else f"CSV {Path(v['csv']).name}")
            summary = (f"Dry Run is off: real emails go out through Crisp and each sent row is marked. "
                       f"This can't be undone.\n{self.stats.preview()}\n"
                       f"Campaign: {self.campaign_name(v)} · Subject: {v['subject']} · "
                       f"{self._shortcut_text(v)}\n"
                       f"Rows {self.stats.range_from}–{self.stats.range_to} "
                       f"of {self.stats.total} · {source} · {self._limits_text(v)}"
                       + (" · retry failed only" if retry_only else ""))
            ok = show_preview(self, f"Send {n} real email{'s' if n != 1 else ''}?", summary,
                              preview_rows(self.stats.plan, v),
                              confirm=f"Send {n} email{'s' if n != 1 else ''}", danger=True)
            if not ok:
                return
        v["retry_only"] = "true" if retry_only else "false"
        err = self._start_agent(v, dry)
        if err:
            W.inform(self, "Could not start", err)

    def _start_agent(self, v: dict, dry: bool, label: str = "") -> str | None:
        """Run folder, settings.json, run.log, then start the agent on the main runner."""
        v["history_file"] = str(self.history_file)
        v["pace_file"] = str(self.data_dir / "pace.json")
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
        self.runner.run_dir = str(self.run_dir or "")
        if err:
            self._close_run_log()
            return err
        self.last_state = None
        self.activity.error_banner.hide()
        self.activity.unconfirmed_banner.hide()
        self.activity.retry_banner.hide()
        self.dashboard.unconfirmed_banner.hide()
        self.prefs.set_recovery(email="", csv=v["csv"], started=f"{datetime.now():%Y-%m-%d %H:%M}",
                                mode="dry run" if dry else "real sending")
        self._recovery_email = ""
        self._gui_line(f"Run started {datetime.now():%Y-%m-%d %H:%M:%S} "
                       f"({'DRY RUN, nothing will be sent' if dry else 'REAL SENDING'})"
                       + (f" · {label}" if label else ""))
        self._set_inputs_locked(True)
        self.navigate(2)
        self._render_state(self.runner.state)
        return None

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
        if s and s.paused and self.runner.is_running():
            text, tone = "Paused", "warning"
        self.status.set(text, tone)
        self._style_run_button()
        a.stop.setEnabled(self.runner.is_running())
        a.stop.setText("Force stop" if phase == rs.STOPPING else "Stop")
        paused = bool(s and s.paused)
        a.pause.setEnabled(self.runner.is_running() and phase in (rs.RUNNING, rs.WAITING_LOGIN))
        a.pause.setText("Resume" if paused or self.runner.pause_requested() else "Pause")
        if self.runner.is_running():
            if not self.eta_timer.isActive():
                self.eta_timer.start()
        else:
            self.eta_timer.stop()
        self._render_eta(s)

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
        if self._results_check and s is not None:
            self._render_check_progress(s)
        elif self.runner.is_running() and s.current_email and s.current_email != getattr(self, "_recovery_email", ""):
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
        if self._results_check:
            self._status_check_finished(s)
            return
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
        self.queue_ctl.runner_finished(self.runner, s)
        QApplication.alert(self)

    # ============================================================ campaign results
    def _selected_campaign(self) -> str:
        r = self.results_page
        return "" if r.campaign.currentIndex() <= 0 else r.campaign.currentText()

    def refresh_results(self):
        """Load history.csv sends into the results store and redraw the page."""
        r = self.results_page
        if self.results.sync_history(sc.read_history(self.history_file)):
            self.results.save()
        current = self._selected_campaign()
        names = self.results.campaigns()
        r.campaign.blockSignals(True)
        r.campaign.clear()
        r.campaign.addItem("All campaigns")
        r.campaign.addItems(names)
        if current in names:
            r.campaign.setCurrentText(current)
        r.campaign.blockSignals(False)
        campaign = self._selected_campaign()
        rows = self.results.rows(campaign)
        summ = cr.summary(rows)
        r.m_sent.set(summ["sent"], f"{summ['unconfirmed']} unconfirmed" if summ["unconfirmed"] else "Real sends")
        r.m_delivered.set(f"{summ['delivered_pct']}%", f"{summ['delivered']} delivered")
        r.m_read.set(f"{summ['read_pct']}%", f"{summ['read']} read (approximate)")
        r.m_replied.set(f"{summ['replied_pct']}%", f"{summ['replied']} replied (reliable)")
        r.m_failed.set(summ["failed"], "Not delivered")
        r.summary.setText(cr.summary_text(summ) if rows else
                          "No real sends recorded yet. Results appear here after a live run.")
        r.read_note.setText(cr.READ_NOTE)
        last = self.results.last_checked(campaign)
        missing = sum(1 for x in rows if not x.get("link"))
        r.last_checked.setText((f"Last checked: {last}" if last else "Not checked yet")
                               + (f" · {missing} without a conversation link (sent before links were recorded; "
                                  "they cannot be checked)" if missing else ""))
        due = cr.recheck_suggestions(rows, last) if rows else []
        r.suggest.setVisible(bool(due))
        if due:
            r.suggest.set_text(f"Re-check suggested: it is now more than {due[0]} since this campaign was sent.")
        self._fill_filter_combo(r.f_status, ["All statuses"] + cr.STATUSES)
        self._fill_filter_combo(r.f_version, ["All versions"] + sorted({x.get("version") or "(none)" for x in rows}))
        self._fill_results_table()
        self._fill_side_tables(rows, campaign)

    @staticmethod
    def _fill_filter_combo(combo, items):
        current = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        if current in items:
            combo.setCurrentText(current)
        combo.blockSignals(False)

    def _filtered_results(self) -> list[dict]:
        r = self.results_page
        rows = self.results.rows(self._selected_campaign())
        status, version, when = r.f_status.currentText(), r.f_version.currentText(), r.f_date.currentIndex()
        if status and status != "All statuses":
            rows = [x for x in rows if x["status"] == status]
        if version and version != "All versions":
            rows = [x for x in rows if (x.get("version") or "(none)") == version]
        if when > 0:
            days = {1: 1, 2: 7, 3: 30}[when]
            cutoff = cr.fmt(datetime.now() - timedelta(days=days))
            rows = [x for x in rows if x.get("sent_at", "") >= cutoff]
        return rows

    @staticmethod
    def _set_rows(table, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, values in enumerate(rows):
            for j, value in enumerate(values):
                table.setItem(i, j, QTableWidgetItem(str(value)))
        table.setSortingEnabled(True)
        table.resizeColumnsToContents()

    def _fill_results_table(self):
        self._set_rows(self.results_page.table, [cr.table_row(x) for x in self._filtered_results()])

    def _fill_side_tables(self, rows, campaign):
        r = self.results_page
        follow = [x for x in rows if x["status"] == cr.REPLIED and x.get("open") is not False]
        self._set_rows(r.followup, [[x["email"], x.get("name", ""), x.get("reply_at", ""), x["campaign"],
                                     x.get("version", ""), x.get("link", "")] for x in follow])
        r.tabs.setTabText(1, f"Needs follow-up ({len(follow)})")
        failed = [x for x in rows if x["status"] == cr.FAILED]
        self._set_rows(r.problems, [[x["email"], x.get("name", ""), x.get("reason", ""), x.get("sent_at", ""),
                                     x["campaign"], x.get("link", "")] for x in failed])
        r.tabs.setTabText(2, f"Delivery problems ({len(failed)})")
        r.add_dnc.setEnabled(bool(failed))

        def pct_row(name, s_):
            return [name, s_["sent"], s_["delivered_pct"], s_["read_pct"], s_["replied_pct"], s_["failed"]]
        self._set_rows(r.compare_campaigns, [pct_row(g["campaign"], g)
                                             for g in cr.compare(self.results.rows(), "campaign")])
        self._set_rows(r.compare_versions, [pct_row(g["version"], g) for g in cr.compare(rows, "version")])
        self._set_rows(r.checks, [[h["at"], h["campaign"], h["sent"], h["delivered"], h["read"], h["replied"],
                                   h["failed"]] for h in reversed(self.results.history(campaign))])

    def _open_link_cell(self, table, row, col):
        header = table.horizontalHeaderItem(col)
        item = table.item(row, col)
        if header and header.text() == "Conversation link" and item and item.text().startswith("http"):
            QDesktopServices.openUrl(QUrl(item.text()))

    def refresh_statuses(self):
        """Open each recorded conversation link in Crisp (read-only) and save the statuses."""
        if self.runner.is_running() or self.updater.busy:
            W.inform(self, "Busy", "Wait until the current run or check has finished.")
            return
        self.refresh_results()
        campaign = self._selected_campaign()
        rows = [x for x in self.results.rows(campaign) if x.get("link")]
        if not rows:
            W.inform(self, "Nothing to check",
                     "No conversation links are recorded for this campaign yet. Links are saved for real sends "
                     "from version 3.7 on.")
            return
        minutes = max(1, round(len(rows) * 9 / 60))
        if not W.ask(self, f"Check {len(rows)} conversation(s) in Crisp?",
                     "Each conversation is opened and read in Chromium, one every few seconds. Nothing is typed, "
                     "sent, resolved or changed. The check stops by itself if the Crisp login is lost.",
                     details=[("Campaign", campaign or "All campaigns"), ("About", f"{minutes} min")],
                     confirm="Start check"):
            return
        folder = self.data_dir / "results_check"
        folder.mkdir(parents=True, exist_ok=True)
        inp, out = folder / "input.json", folder / "observed.jsonl"
        inp.write_text(json.dumps([{"key": cr.rkey(x["campaign"], x["email"]), "email": x["email"],
                                    "link": x["link"], "sent_at": x.get("sent_at", "")} for x in rows]),
                       encoding="utf-8")
        out.unlink(missing_ok=True)
        v = self.campaign_values()
        v.update({"results_check": "true", "results_input": str(inp), "results_output": str(out),
                  "run_dir": "", "history_file": ""})
        err = self.runner.start(v, True, self.profile_dir)
        if err:
            W.inform(self, "Could not start the check", err)
            return
        self._results_check, self._results_campaign = True, campaign
        r = self.results_page
        r.refresh.setEnabled(False)
        r.stop.show()
        r.progress.setValue(0)
        r.progress.show()
        r.progress_text.setText("Opening Crisp… sign in in the Chromium window if asked.")
        self._gui_line(f"Status check started for {len(rows)} conversation(s), read-only.")

    def _stop_status_check(self):
        if self.runner.is_running() and self._results_check:
            self.runner.request_stop()
            self.results_page.progress_text.setText("Stopping after the current page…")

    def _render_check_progress(self, s):
        r = self.results_page
        if s.eligible:
            r.progress.setValue(int(1000 * min(s.position, s.eligible) / s.eligible))
        r.progress_text.setText(s.step or "Checking…")

    def _status_check_finished(self, s):
        self._results_check = False
        out = self.data_dir / "results_check" / "observed.jsonl"
        merged = 0
        try:
            lines = out.read_text(encoding="utf-8").splitlines() if out.exists() else []
        except OSError:
            lines = []
        for line in lines:
            try:
                item = json.loads(line)
                when = datetime.strptime(item.get("observed_at", ""), "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            if item.get("key") in self.results.recipients:
                self.results.apply(item["key"], item.get("result") or {}, when)
                merged += 1
        if merged:
            self.results.record_check(self._results_campaign or "(all)")
        self.results.save()
        r = self.results_page
        r.refresh.setEnabled(True)
        r.stop.hide()
        r.progress.hide()
        r.progress_text.setText(f"Checked {merged} conversation(s)."
                                + (f" {s.last_error}" if s.last_error else ""))
        self._gui_line(f"Status check finished: {merged} conversation(s) read.")
        self.last_state = s
        self._render_state(s)
        self.refresh_results()
        if s.last_error:
            W.inform(self, "Status check stopped", s.last_error)

    def export_results(self):
        rows = self._filtered_results()
        if not rows:
            W.inform(self, "Nothing to export", "No rows match the current filters.")
            return
        target, _ = QFileDialog.getSaveFileName(self, "Export campaign results",
                                                str(Path.home() / "campaign_results.csv"), "CSV files (*.csv)")
        if target:
            cr.export_csv(rows, Path(target))
            self.flash(self.results_page.export, "Exported")

    def add_failed_to_dnc(self):
        """Never automatic: the user confirms, and chooses the list file if none is set."""
        failed = sorted({x["email"] for x in self.results.rows(self._selected_campaign()) if x["status"] == cr.FAILED})
        if not failed:
            return
        path = self.campaign.dnc.text()
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Do-not-contact list",
                                                  str(self.data_dir / "do_not_contact.csv"), "CSV files (*.csv)")
            if not path:
                return
        if not W.ask(self, f"Add {len(failed)} email(s) to the do-not-contact list?",
                     "They will be skipped in every future run.", details=[("List", path)], confirm="Add"):
            return
        target = Path(path)
        existing = rules_dnc = set()
        if target.exists():
            try:
                existing = {line.strip().lower() for line in target.read_text(encoding="utf-8-sig").splitlines()}
            except OSError:
                existing = set()
        with target.open("a", encoding="utf-8", newline="") as handle:
            if not target.exists() or target.stat().st_size == 0:
                handle.write("Email\n")
            for email in failed:
                if email not in existing and email not in rules_dnc:
                    handle.write(email + "\n")
        self.campaign.dnc.edit.setText(str(target))
        self.refresh_csv()
        self.flash(self.results_page.add_dnc, "Added")

    # ============================================================ preview / pause / ETA
    # ============================================================ campaigns and queue
    def prefs_parallel(self) -> int:
        return self.prefs.parallel()

    def _save_parallel(self):
        try:
            self.prefs.set_parallel(int(self.settings.parallel.text() or 1))
            self.settings.parallel.set_state("help")
        except ValueError:
            self.settings.parallel.set_state("error", "Enter 1, 2 or 3.")
        self.settings.parallel.edit.setText(str(self.prefs.parallel()))
        self.refresh_campaigns()

    def _campaign_action(self, fn):
        cid = self.campaigns_page.selected_id()
        if not cid:
            W.inform(self, "Choose a campaign", "Select a campaign in the list first.")
            return
        try:
            result = fn(cid)
        except ValueError as exc:
            W.inform(self, "Not possible", str(exc))
            return
        self.refresh_campaigns(select=getattr(result, "id", cid))

    def save_campaign_new(self):
        v = self.campaign_values()
        name = self.campaign.campaign_name.text() or v.get("subject", "")
        c = self.campaign_store.add(name, v)
        self.prefs.set_editing_campaign(c.id)
        self._gui_line(f"Campaign '{c.name}' saved ({c.label}).")
        self.refresh_campaigns(select=c.id)

    def update_campaign(self):
        def do(cid):
            c = self.campaign_store.update(cid, self.campaign_values())
            self._gui_line(f"Campaign '{c.name}' updated from Campaign Setup.")
            return c
        self._campaign_action(do)

    def open_campaign(self):
        cid = self.campaigns_page.selected_id()
        c = self.campaign_store.get(cid)
        if c is None:
            W.inform(self, "Choose a campaign", "Select a campaign in the list first.")
            return
        if self.runner.is_running():
            W.inform(self, "A run is in progress", "Campaign Setup is locked while a run is in progress.")
            return
        self._apply_campaign_values(c.settings)
        self.prefs.set_editing_campaign(c.id)
        self.navigate(1)

    def _apply_campaign_values(self, values: dict):
        c = self.campaign
        for key, f in c.fields().items():
            if key in values and key not in cs.APP_WIDE:
                f.edit.setText(values[key])
        c.skip_failed.setChecked(values.get("skip_failed") == "true")
        source = values.get("source", "csv")
        self.prefs.set_source(source)
        self._apply_source(source)
        tab = values.get("sheet_tab", "")
        if tab:
            if c.sheet_tab.findText(tab) < 0:
                c.sheet_tab.addItem(tab)
            c.sheet_tab.setCurrentText(tab)
        self.refresh_csv()

    def delete_campaign(self):
        cid = self.campaigns_page.selected_id()
        c = self.campaign_store.get(cid)
        if c is None:
            return
        if W.ask(self, f"Delete campaign '{c.name}'?", "Its saved settings are removed. History, results and "
                 "your recipient files are kept.", confirm="Delete", tone="danger"):
            self._campaign_action(self.campaign_store.remove)

    def pause_campaign(self):
        cid = self.campaigns_page.selected_id()
        c = self.campaign_store.get(cid)
        if c is None:
            return
        if c.status == cs.PAUSED:
            self.queue_ctl.resume(cid)
        elif not self.queue_ctl.pause(cid):
            W.inform(self, "Not paused", "This campaign cannot be paused right now.")
        self.refresh_campaigns(select=cid)

    def stop_campaign(self):
        cid = self.campaigns_page.selected_id()
        if self.queue_ctl.stop(cid) == "idle":
            W.inform(self, "Not running", "Only a running campaign can be stopped. To skip a queued one, "
                     "remove it from the queue or pause it.")
        else:
            self._gui_line("Stop requested for this campaign: it stops before its next recipient.")
        self.refresh_campaigns(select=cid)

    def _campaign_plan(self, c):
        """(emails to send, duplicates, note, error) for the queue summary."""
        s = c.settings
        path = s.get("csv", "")
        if s.get("source") == "sheet":
            fresh = getattr(self, "_sheet_preview_key", None) == (s.get("sheet_url"), s.get("sheet_tab"))
            if not (fresh and self._sheet_preview_path().exists()):
                return [], 0, "Google Sheet: counted when it starts", ""
            path = str(self._sheet_preview_path())
        try:
            recent = max(1, int(s.get("recent_days") or 7))
        except ValueError:
            recent = 7
        shared = str(self._sheet_shared_path()) if s.get("source") == "sheet" else ""
        st = read_stats(path, s.get("reference", ""), s.get("row_from", ""), s.get("row_to", ""), s.get("dnc", ""),
                        history_file=str(self.history_file), campaign=c.name, recent_days=recent,
                        shared_file=shared, claim_expiry=self._shared_values()["claim_expiry"])
        if not st.ok:
            return [], 0, "", st.error or "The recipient list could not be read."
        if st.range_error or st.dnc_error:
            return [], 0, "", st.range_error or st.dnc_error
        return [p["email"] for p in st.plan if p["send"]], st.duplicates, "", ""

    def run_queue(self):
        store = self.campaign_store
        if self.queue_ctl.active:
            W.inform(self, "The queue is already running", "Stop it first to change what runs.")
            return
        if self.runner.is_running() and self.prefs.parallel() == 1:
            W.inform(self, "A run is in progress", "Wait until it ends, then run the queue.")
            return
        queued = [c for c in store.list() if c.status == cs.QUEUED]
        if not queued:
            W.inform(self, "Nothing queued", "Select campaigns and click Add to queue first.")
            return
        plans, problems = {}, []
        for c in queued:
            emails, dups, note, error = self._campaign_plan(c)
            if error:
                problems.append(f"{c.name}: {error}")
            plans[c.id] = (emails, dups, note)
        if problems:
            W.inform(self, "Fix these campaigns first", "\n".join(problems))
            return
        v = self.campaign_values()
        dry = self.dry_run()
        try:
            delay = 40.0 if v.get("random_delay") == "true" else float(v.get("delay") or 3)
        except ValueError:
            delay = 3.0
        cap = sc.optional_int(v.get("daily_cap", ""), "Daily cap") if v.get("daily_cap") else None
        summ = cs.queue_summary(queued, plans, delay, cap, sc.sent_today(self.history_file) if not dry else 0)
        k = len(queued)
        rows = [(str(i + 1), r["name"], r["subject"], r["shortcut"], str(r["listed"]), str(r["duplicates"]),
                 str(r["overlap"]), str(r["to_send"]) if not r["note"] else r["note"])
                for i, r in enumerate(summ["rows"])]
        parallel = self.prefs.parallel()
        lines = [("DRY RUN: nothing is sent." if dry else
                  "REAL SENDING: emails go out through Crisp and each sent row is marked. This can't be undone."),
                 f"{k} campaign(s), {summ['total']} email(s) in total, {cs.duration_text(summ['seconds'])} "
                 f"(delay {delay:.0f} s between emails, shared by all campaigns)."]
        if summ["overlaps"]:
            lines.append(f"{len(summ['overlaps'])} person(s) are in more than one campaign: only the first "
                         "campaign emails them.")
        if cap is not None and summ["capped"] < summ["total"]:
            lines.append(f"Daily cap {cap}: only {summ['capped']} can go out today; the queue stops there.")
        if any(r["note"] for r in summ["rows"]):
            lines.append("Google Sheet campaigns are counted when they start (click Test connection to count now).")
        if parallel > 1:
            lines.append(f"{parallel} campaigns run at the same time, each in its own browser window and Crisp "
                         "login, all on your Crisp account and the same sending limits.")
        columns = ("#", "Campaign", "Subject", "Shortcut(s)", "Listed", "Duplicates", "In an earlier campaign",
                   "Will send")
        n = summ["total"]
        names = ", ".join(c.name for c in queued)
        if dry:
            title, confirm = f"Dry run {k} campaign(s)?", "Start dry run"
        else:
            title = f"Send {n} real email{'s' if n != 1 else ''} in {k} campaign{'s' if k != 1 else ''}?"
            confirm = f"Send {n} emails: {names}" if len(names) < 60 else f"Send {n} emails in {k} campaigns"
        if not show_preview(self, title, "\n".join(lines), rows, confirm=confirm, danger=not dry, columns=columns):
            return
        self.queue_ctl.start(dry)

    def launch_campaign(self, c, runner, slot: int, dry: bool) -> str | None:
        """Start one campaign (called by the queue). App-wide pace values come from the app."""
        v = dict(c.settings)
        current = self.campaign_values()
        for key in cs.APP_WIDE:
            v[key] = current.get(key, "")
        v.update(self._shared_values())
        v["data_dir"] = str(self.data_dir)
        v["campaign"] = c.name
        v["retry_only"] = "false"
        v["workcopy_tag"] = c.id
        try:
            v["recent_days"] = str(max(1, int(v.get("recent_days") or 7)))   # never twice in a day across campaigns
        except ValueError:
            v["recent_days"] = "7"
        if slot == 0:
            return self._start_agent(v, dry, label=f"campaign '{c.name}'")
        v["history_file"] = str(self.history_file)
        v["pace_file"] = str(self.data_dir / "pace.json")
        try:
            run_dir = sc.new_run_dir(self.runs_root, dry)
        except FileExistsError:
            run_dir = sc.new_run_dir(self.runs_root / f"browser{slot + 1}", dry)
        v["run_dir"] = str(run_dir)
        settings = dict(v, dry_run=dry, app_version=APP_VERSION, started=sc.stamp())
        (run_dir / "settings.json").write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        runner.run_log = (run_dir / "run.log").open("a", encoding="utf-8")
        runner.run_dir = str(run_dir)
        profile = self.profile_dir.with_name(f"{self.profile_dir.name}_{slot + 1}")
        err = runner.start(v, dry, profile)
        if err:
            runner.run_log.close()
            runner.run_log = None
        return err

    def _queue_progress(self):
        if self.queue_ctl.busy():
            self.refresh_campaigns()

    def refresh_campaigns(self, select: str = ""):
        cp = self.campaigns_page
        select = select or cp.selected_id()
        store = self.campaign_store
        items = store.list()
        cp.table.setRowCount(len(items))
        for r, c in enumerate(items):
            s = c.settings
            pos = str(store.queue.index(c.id) + 1) if c.id in store.queue else ""
            status = c.label
            runner = self.queue_ctl.runner_of(c.id)
            st = runner.state if runner is not None else None
            if st is not None and runner.is_running():
                status += f" · {st.processed}/{st.eligible or '?'}"
                if self.queue_ctl.slot_of(c.id):
                    status += f" · browser {self.queue_ctl.slot_of(c.id) + 1}"
            last = c.last
            last_text = ""
            if last:
                last_text = (("dry run: " if last.get("dry_run") else "") + f"{last.get('completed', 0)} done, "
                             f"{last.get('failed', 0)} failed"
                             + (f", {last.get('elsewhere')} by others" if last.get("elsewhere") else ""))
            source = (f"Sheet · {s.get('sheet_tab', '')}" if s.get("source") == "sheet"
                      else Path(s.get("csv", "")).name)
            values = (pos, c.name, status, source, s.get("subject", ""), s.get("shortcut", ""), last_text, c.note)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                if col == 1:
                    item.setData(Qt.UserRole, c.id)
                if col == 2 and c.status in (cs.UNCONFIRMED, cs.FAILED):
                    item.setText("⚠ " + text)
                cp.table.setItem(r, col, item)
            if c.id == select:
                cp.table.selectRow(r)
        cp.table.resizeColumnsToContents()
        cp.empty.setVisible(not items)
        notes = list(getattr(self, "_restore_notes", []))
        unconfirmed = [c.name for c in items if c.status == cs.UNCONFIRMED]
        if unconfirmed:
            notes.append("Unconfirmed: " + ", ".join(unconfirmed) + ". Check the last conversation in Crisp, "
                         "then click I checked Crisp.")
        cp.banner.set_text(" ".join(dict.fromkeys(notes)))
        cp.banner.setVisible(bool(notes))
        live = not self.dry_run()
        cp.mode_banner.set_text("REAL SENDING is on: the queue sends real emails."
                               if live else "Dry Run is on: the queue checks everything but sends nothing.")
        running = [c.name for c in store.running()]
        queued = sum(1 for c in items if c.status == cs.QUEUED)
        cp.queue_status.setText(
            ("Queue running. " if self.queue_ctl.active else "Queue idle. ")
            + (f"Running: {', '.join(running)}. " if running else "")
            + f"{queued} queued. "
            + ("One campaign at a time." if self.prefs.parallel() == 1
               else f"Up to {self.prefs.parallel()} at the same time."))
        self._update_campaign_buttons()

    def _update_campaign_buttons(self):
        cp = self.campaigns_page
        c = self.campaign_store.get(cp.selected_id())
        has = c is not None
        running = has and self.queue_ctl.runner_of(c.id) is not None
        for b in (cp.update_sel, cp.open_sel, cp.duplicate):
            b.setEnabled(has)
        cp.delete.setEnabled(has and not running)
        cp.enqueue.setEnabled(has and c.status not in (cs.QUEUED, cs.RUNNING, cs.PAUSED, cs.UNCONFIRMED))
        cp.dequeue.setEnabled(has and c.status in (cs.QUEUED, cs.PAUSED) and not running)
        in_queue = has and c.id in self.campaign_store.queue
        cp.up.setEnabled(in_queue)
        cp.down.setEnabled(in_queue)
        cp.run_next.setEnabled(has and not running and c.status != cs.UNCONFIRMED)
        cp.pause.setEnabled(has and c.status in (cs.QUEUED, cs.PAUSED, cs.RUNNING))
        cp.pause.setText("Resume" if has and c.status == cs.PAUSED else "Pause")
        cp.stop.setEnabled(running)
        cp.checked.setEnabled(has and c.status == cs.UNCONFIRMED)
        cp.run_queue.setEnabled(not self.queue_ctl.active)
        cp.stop_queue.setEnabled(self.queue_ctl.active)

    def campaign_name(self, v: dict | None = None) -> str:
        v = v or self.campaign_values()
        return (v.get("campaign") or "").strip() or (v.get("subject") or "").strip()

    def show_row_preview(self):
        """Per-row table of what a run would do. Sends nothing, writes nothing."""
        self.refresh_csv()
        st = self.stats
        if not st.ok or st.range_error:
            W.inform(self, "Nothing to preview", st.error or st.range_error or "Check the campaign settings.")
            return
        v = self.campaign_values()
        show_preview(self, "Preview: what a run would do", st.preview(), preview_rows(st.plan, v))

    def toggle_pause(self):
        if not self.runner.is_running():
            return
        if self.runner.pause_requested():
            self.runner.resume()
            self._gui_line("Resume requested.")
        elif self.runner.pause():
            self._gui_line("Pause requested: the run pauses after the current recipient (never mid-send).")
            self.activity.step.setText("Pausing after the current recipient…")
        self._render_state(self.runner.state)

    def _render_eta(self, s):
        a, d = self.activity, self.dashboard
        if s is None or not self.runner.is_running() or not s.eligible:
            a.eta.setText("")
            return
        if s.paused:
            a.eta.setText(f"Paused after {s.paused_after}. Press Resume to continue with the next recipient.")
            return
        seconds = rs.eta_seconds(s, time.monotonic())
        finish = datetime.now() + timedelta(seconds=seconds)
        mins = max(1, round(seconds / 60))
        text = (f"{s.processed} of {s.eligible} done · ETA {finish:%H:%M} (about {mins} min)"
                if seconds > 0 else f"{s.processed} of {s.eligible} done")
        a.eta.setText(text)
        d.run_progress_text.setText(text)

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
        extra = [r for _cid, r in self.queue_ctl.slots.values() if r is not self.runner and r.is_running()]
        if any(not (r.state and r.state.paused) for r in extra) and not (
                self.runner.is_running() and not (self.runner.state and self.runner.state.paused)):
            if not W.ask(self, "Quit while campaigns are running?",
                         "Quitting stops them immediately. A campaign that was sending is marked "
                         "\"Unconfirmed\": check its last conversation in Crisp before running it again.",
                         confirm="Stop and quit", tone="danger"):
                event.ignore()
                return
        state = self.runner.state
        if self.runner.is_running() and not (state is not None and state.paused):
            if not W.ask(self, "Quit while the agent is running?",
                         "Quitting stops the agent immediately. If an email is being sent right now, the CSV row "
                         "might not get marked. Check the last conversation in Crisp before running again.",
                         confirm="Stop and quit", tone="danger"):
                event.ignore()
                return
        self.queue_ctl.close_all()
        if self.runner.is_running() and state is not None and state.paused:
            # Paused = between two recipients, nothing in flight: close like a stopped run.
            self.runner.request_stop()
            self.runner.force_stop()
            self.runner.proc.waitForFinished(5000)
            self.prefs.clear_recovery()
            self.prefs.set_last_run({"finished_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                                     "mode": "dry" if state.dry_run else "real", "outcome": "stopped",
                                     "sent": state.sent_confirmed, "completed": state.completed,
                                     "failed": state.failed, "eligible": state.eligible,
                                     "error": f"Closed while paused after {state.paused_after}"})
        elif self.runner.is_running():
            self.runner.force_stop()
            self.runner.proc.waitForFinished(5000)
        self.prefs.set_geometry(self.saveGeometry())
        self.prefs.sync()
        event.accept()

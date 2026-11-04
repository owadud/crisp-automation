"""Paths, defaults and persisted preferences."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

APP_NAME = "Crisp Automation"

ROOT = Path(__file__).resolve().parent.parent


def read_version(root: Path = ROOT) -> str:
    """The VERSION file is the single source of truth for the app version."""
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


APP_VERSION = read_version()
# Public GitHub repo whose Releases the in-app updater reads.
GITHUB_REPO = "owadud/crisp-automation"
AGENT_SCRIPT = ROOT / "crisp_email_agent.py"
# Same location the previous GUI and the agent use, so the Crisp login
# session is preserved.
PROFILE_DIR = Path.home() / ".crisp_email_agent" / "browser_profile"

CAMPAIGN_DEFAULTS = {
    "csv": "",  # empty on purpose: user picks a file; never ship real data
    "subject": "[Action Required] Exclusive VIP Access👀",
    "segment": "pending-cart",
    "reference": "Shahriar",
    "shortcut": "recommend-offer",
    "row_from": "",  # blank = from the first row
    "row_to": "",    # blank = to the last row
    "dnc": "",                 # optional do-not-contact CSV
    "max_skips": "5",          # used only when "skip and continue" is on
    "max_fails_in_row": "3",   # auto-stop after this many failures in a row
}

THEMES = ("light", "dark", "system")


class Preferences:
    """Small typed wrapper around QSettings.

    Dry Run is deliberately NOT persisted: every launch starts in Dry Run,
    so real sending always needs a deliberate choice.
    """

    def __init__(self, settings: QSettings | None = None):
        self._s = settings or QSettings("CrispAutomation", "CrispAutomationDesktop")

    # campaign ------------------------------------------------------------
    def campaign(self) -> dict:
        data = {}
        for key, default in CAMPAIGN_DEFAULTS.items():
            value = self._s.value(f"campaign/{key}", default)
            data[key] = str(value) if value is not None else default
        return data

    def set_campaign_value(self, key: str, value: str) -> None:
        if key in CAMPAIGN_DEFAULTS:
            self._s.setValue(f"campaign/{key}", value)

    def reset_campaign(self) -> dict:
        for key in CAMPAIGN_DEFAULTS:
            self._s.remove(f"campaign/{key}")
        return dict(CAMPAIGN_DEFAULTS)

    # crash recovery --------------------------------------------------------
    def recovery(self) -> dict | None:
        """Set while a run is active; still set at startup = the app did not exit cleanly."""
        if str(self._s.value("recovery/active", "0")) != "1":
            return None
        return {k: str(self._s.value(f"recovery/{k}", "")) for k in ("email", "csv", "started", "mode")}

    def set_recovery(self, **values) -> None:
        self._s.setValue("recovery/active", "1")
        for key, value in values.items():
            self._s.setValue(f"recovery/{key}", value)
        self._s.sync()

    def clear_recovery(self) -> None:
        self._s.remove("recovery")
        self._s.sync()

    # theme ---------------------------------------------------------------
    def theme(self) -> str:
        value = str(self._s.value("ui/theme", "light"))
        return value if value in THEMES else "light"

    def set_theme(self, value: str) -> None:
        if value in THEMES:
            self._s.setValue("ui/theme", value)

    # window --------------------------------------------------------------
    def geometry(self):
        return self._s.value("ui/geometry")

    def set_geometry(self, value) -> None:
        self._s.setValue("ui/geometry", value)

    # last run ------------------------------------------------------------
    def last_run(self) -> dict | None:
        if not self._s.contains("last_run/finished_at"):
            return None
        keys = ("finished_at", "mode", "outcome", "sent", "completed", "failed", "eligible", "error")
        return {k: self._s.value(f"last_run/{k}", "") for k in keys}

    def set_last_run(self, data: dict) -> None:
        for key, value in data.items():
            self._s.setValue(f"last_run/{key}", value)

    def sync(self) -> None:
        self._s.sync()

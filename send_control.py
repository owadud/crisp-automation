"""Sending limits, Dhaka time, send window, history.csv and run folders.

Shared by crisp_email_agent.py and the GUI. No third-party imports.
Dhaka is UTC+6 all year (no daylight saving), so a fixed offset is exact and
works on Windows without the tzdata package.
"""

from __future__ import annotations

import csv
import re
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DHAKA = timezone(timedelta(hours=6), "Dhaka")
HISTORY_COLUMNS = ["Time", "Email", "Name", "Result", "Campaign", "Subject", "Mode", "Error", "Row", "CSV"]
# Results written to history.csv / report.csv / the CSV Status column.
SENT, DRY_RUN, FAILED, SKIPPED, UNCONFIRMED, INVALID = (
    "Sent", "Dry run", "Failed", "Skipped", "Unconfirmed", "Invalid")


def now_dhaka() -> datetime:
    return datetime.now(DHAKA)


def stamp(dt: datetime | None = None) -> str:
    return (dt or now_dhaka()).strftime("%Y-%m-%d %H:%M:%S")


# ------------------------------------------------------------------ time
def parse_hhmm(text: str) -> tuple[int, int]:
    """'9:05' / '09:05' / '9.05' -> (9, 5). Raises ValueError."""
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", str(text or ""))
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Use HH:MM, for example 10:00 (got {text!r}).")
    return int(m.group(1)), int(m.group(2))


def parse_window(text: str):
    """'10:00-18:00' -> ((10, 0), (18, 0)); blank -> None. Raises ValueError."""
    if not str(text or "").strip():
        return None
    parts = re.split(r"\s*[-–]\s*", str(text).strip())
    if len(parts) != 2:
        raise ValueError("Use HH:MM-HH:MM, for example 10:00-18:00.")
    start, end = parse_hhmm(parts[0]), parse_hhmm(parts[1])
    if start == end:
        raise ValueError("Start and end of the window must differ.")
    return start, end


def _minutes(hm) -> int:
    return hm[0] * 60 + hm[1]


def in_window(now: datetime, window) -> bool:
    if window is None:
        return True
    cur, start, end = now.hour * 60 + now.minute, _minutes(window[0]), _minutes(window[1])
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end      # overnight window, e.g. 22:00-02:00


def window_opens_later_today(now: datetime, window) -> datetime | None:
    """When the window opens later today (now is before it), else None."""
    if window is None or in_window(now, window):
        return None
    start = window[0]
    opens = now.replace(hour=start[0], minute=start[1], second=0, microsecond=0)
    return opens if opens > now else None


def next_occurrence(hhmm: str, now: datetime | None = None) -> datetime:
    """Next time the clock shows HH:MM in Dhaka (today, or tomorrow if passed)."""
    now = now or now_dhaka()
    h, m = parse_hhmm(hhmm)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target if target > now else target + timedelta(days=1)


def random_delay(rng=None) -> float:
    import random
    return (rng or random).uniform(20, 60)


def sleep_until(target: datetime, stop=lambda: False, step: float = 5.0, clock=now_dhaka) -> bool:
    """Wait until target. Returns False if stop() became true first."""
    while clock() < target:
        if stop():
            return False
        time.sleep(min(step, max(0.05, (target - clock()).total_seconds())))
    return True


def sleep_seconds(seconds: float, stop=lambda: False) -> bool:
    end = time.monotonic() + max(0.0, seconds)
    while time.monotonic() < end:
        if stop():
            return False
        time.sleep(min(0.5, end - time.monotonic()) if end > time.monotonic() else 0)
    return True


def optional_int(text, name: str) -> int | None:
    """Blank -> None (no limit). Raises ValueError with a readable message."""
    value = str(text or "").strip()
    if not value:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError(f"{name} must be a whole number of 1 or more.")
    return int(value)


# ------------------------------------------------------------------ history
def _upgrade_history(path: Path) -> None:
    """Rewrite an older history.csv (fewer columns) with the current header."""
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if list(reader.fieldnames or []) == HISTORY_COLUMNS:
                return
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError):
        return
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def append_history(path: Path, **row) -> None:
    path = Path(path)
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not new:
        _upgrade_history(path)
    if row.get("Email"):
        row["Email"] = str(row["Email"]).strip().lower()
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_COLUMNS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({"Time": stamp(), **row})


def read_history(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []


def count_sent(path: Path, since: datetime) -> int:
    """Real (not dry-run) sends recorded at or after `since` (Dhaka)."""
    cutoff = stamp(since)
    return sum(1 for r in read_history(path)
               if r.get("Result") == SENT and r.get("Mode") == "live" and r.get("Time", "") >= cutoff)


class HistoryIndex:
    """Who was emailed before, from history.csv (all campaigns, all files).

    Only real (live) sends count: Sent, and Unconfirmed as "possibly sent".
    Dry runs never count.
    """

    def __init__(self, path: Path, campaign: str = "", days: int = 0, now: datetime | None = None):
        self.campaign = str(campaign or "").strip().lower()
        self.days = max(0, int(days or 0))
        cutoff = stamp((now or now_dhaka()) - timedelta(days=self.days)) if self.days else None
        self._campaign: dict = {}
        self._recent: dict = {}
        for r in read_history(path):
            result = r.get("Result", "")
            if r.get("Mode") != "live" or result not in (SENT, UNCONFIRMED):
                continue
            email = str(r.get("Email", "")).strip().lower()
            entry = (r.get("Time", ""), r.get("Campaign", ""), result)
            if self.campaign and str(r.get("Campaign", "")).strip().lower() == self.campaign:
                self._campaign[email] = entry
            if cutoff and entry[0] >= cutoff and entry[0] >= self._recent.get(email, ("",))[0]:
                self._recent[email] = entry

    def campaign_sent(self, email: str):
        return self._campaign.get(str(email or "").strip().lower())

    def recent(self, email: str):
        return self._recent.get(str(email or "").strip().lower())


def sent_today(path: Path, now: datetime | None = None) -> int:
    now = now or now_dhaka()
    return count_sent(path, now.replace(hour=0, minute=0, second=0, microsecond=0))


def sent_last_days(path: Path, days: int = 7, now: datetime | None = None) -> int:
    return count_sent(path, (now or now_dhaka()) - timedelta(days=days))


# ------------------------------------------------------------------ run folders
def new_run_dir(runs_root: Path, dry: bool) -> Path:
    name = f"{now_dhaka():%Y-%m-%d_%H%M%S}_{'dry' if dry else 'live'}"
    path = Path(runs_root) / name
    path.mkdir(parents=True, exist_ok=False)
    return path


def delete_old_runs(runs_root: Path, days: int = 30, now: float | None = None) -> list[Path]:
    """Remove run folders older than `days`. Returns what was removed."""
    root = Path(runs_root)
    if not root.is_dir():
        return []
    cutoff = (now or time.time()) - days * 86400
    removed = []
    for child in root.iterdir():
        if child.is_dir() and re.match(r"^\d{4}-\d{2}-\d{2}_\d{6}", child.name) and child.stat().st_mtime < cutoff:
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child)
    return removed

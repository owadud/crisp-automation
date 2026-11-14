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
HISTORY_COLUMNS = ["Time", "Email", "Name", "Result", "Campaign", "Subject", "Mode", "Error", "Row", "CSV",
                   "Shortcut", "Version", "Link"]
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
def recent_days_value(text, default: int = 7) -> int:
    """'Skip anyone emailed in the last N days': blank -> default, 0 = off."""
    try:
        return max(0, int(str(text if text is not None else "").strip() or default))
    except ValueError:
        return default


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
        self._cutoff = stamp((now or now_dhaka()) - timedelta(days=self.days)) if self.days else None
        self._campaign: dict = {}
        self._recent: dict = {}
        self.blocker = None    # shared Sheet: callable(email) -> reason it is claimed elsewhere
        for r in read_history(path):
            if r.get("Mode") != "live":
                continue
            self.add(r.get("Email", ""), r.get("Time", ""), r.get("Campaign", ""), r.get("Result", ""))

    def add(self, email: str, time_text: str, campaign: str, result: str) -> None:
        """One live send, from history.csv or from the shared Sheet log."""
        if result not in (SENT, UNCONFIRMED):
            return
        email = str(email or "").strip().lower()
        entry = (str(time_text or ""), str(campaign or ""), result)
        if self.campaign and entry[1].strip().lower() == self.campaign:
            if entry[0] >= self._campaign.get(email, ("",))[0]:
                self._campaign[email] = entry
        if self._cutoff and entry[0] >= self._cutoff and entry[0] >= self._recent.get(email, ("",))[0]:
            self._recent[email] = entry

    def claimed(self, email: str) -> str:
        return self.blocker(email) if self.blocker else ""

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


# ------------------------------------------------------------------ A/B shortcut variants
def parse_variants(shortcuts: str, versions: str = "") -> list[tuple[str, str]]:
    """'!a, !b' + 'v1, v2' -> [('!a', 'v1'), ('!b', 'v2')]. Commas or new lines separate.

    One shortcut keeps the old behaviour (its version label may be blank).
    Several shortcuts = an A/B test: a blank label becomes the shortcut name.
    Raises ValueError with a message the user can act on."""
    names = [s.strip() for s in re.split(r"[,\n]", str(shortcuts or "")) if s.strip()]
    labels = [s.strip() for s in re.split(r"[,\n]", str(versions or "")) if s.strip()]
    if not names:
        raise ValueError("Enter a Crisp shortcut.")
    if len({n.lower() for n in names}) != len(names):
        raise ValueError("The same shortcut is listed twice.")
    if len(labels) > len(names):
        raise ValueError(f"{len(labels)} version labels for {len(names)} shortcut(s). "
                         "Give one label per shortcut, in the same order.")
    if len(names) == 1:
        return [(names[0], labels[0] if labels else "")]
    labels += names[len(labels):]
    if len({lab.lower() for lab in labels}) != len(labels):
        raise ValueError("Each shortcut needs a different version label.")
    return list(zip(names, labels))


def split_subjects(text: str) -> list[str]:
    """'Subject A | Subject B' -> ['Subject A', 'Subject B']. | or new lines separate
    (not commas: subjects often contain them)."""
    return [s.strip() for s in re.split(r"[|\n]", str(text or "")) if s.strip()]


def ab_plan(shortcuts: str, versions: str = "", subjects: str = "") -> tuple[list[tuple[str, str]], list[str]]:
    """A/B variants [(shortcut, label)] and the subject for each, same order.

    One subject = every variant uses it (the old behaviour). Several subjects pair
    with the shortcuts in order; with one shortcut, each subject becomes a variant
    (blank labels -> S1, S2, ...). Raises ValueError with a message the user can act on."""
    subs = split_subjects(subjects)
    if len(subs) <= 1:
        variants = parse_variants(shortcuts, versions)
        return variants, [subs[0] if subs else ""] * len(variants)
    names = [s.strip() for s in re.split(r"[,\n]", str(shortcuts or "")) if s.strip()]
    labels = [s.strip() for s in re.split(r"[,\n]", str(versions or "")) if s.strip()]
    if not names:
        raise ValueError("Enter a Crisp shortcut.")
    if len({s.lower() for s in subs}) != len(subs):
        raise ValueError("The same subject is listed twice.")
    if len(names) == 1:
        names = names * len(subs)
        defaults = [f"S{i + 1}" for i in range(len(subs))]
    elif len(names) == len(subs):
        if len({n.lower() for n in names}) != len(names):
            raise ValueError("The same shortcut is listed twice.")
        defaults = names
    else:
        raise ValueError(f"{len(subs)} subjects for {len(names)} shortcuts. Give one subject, "
                         "or one subject per shortcut in the same order.")
    if len(labels) > len(subs):
        raise ValueError(f"{len(labels)} version labels for {len(subs)} subject(s). "
                         "Give one label per subject, in the same order.")
    labels += defaults[len(labels):]
    if len({lab.lower() for lab in labels}) != len(labels):
        raise ValueError("Each version needs a different label.")
    return list(zip(names, labels)), subs


def versions_to_text(rows) -> tuple[str, str, str]:
    """Version table rows [(shortcut, subject, label)] -> (shortcuts, subjects, labels) as saved:
    one subject for all -> 'A'; one shortcut for all -> '!a'; otherwise both listed in order.
    Blank rows are dropped; a blank label becomes v1, v2 ... so labels stay in order."""
    rows = [tuple(str(x or "").strip() for x in r) for r in rows]
    rows = [r for r in rows if any(r)]
    if not rows:
        return "", "", ""
    shortcuts = [r[0].replace(",", " ") for r in rows]
    subjects = [r[1].replace("|", " ") for r in rows]
    labels = [r[2].replace(",", " ") for r in rows]
    if len(rows) == 1:
        return shortcuts[0], subjects[0], labels[0]
    same_sub, same_sc = len(set(subjects)) == 1, len(set(shortcuts)) == 1
    shortcut_text = shortcuts[0] if same_sc and not same_sub else ", ".join(shortcuts)
    subject_text = subjects[0] if same_sub else " | ".join(subjects)
    label_text = ", ".join(lab or f"v{i + 1}" for i, lab in enumerate(labels)) if any(labels) else ""
    return shortcut_text, subject_text, label_text


def versions_from_text(shortcuts: str, subjects: str, labels: str) -> list[tuple[str, str, str]]:
    """The saved texts -> version table rows [(shortcut, subject, label)]."""
    try:
        variants, subs = ab_plan(shortcuts, labels, subjects)
        given = [s.strip() for s in re.split(r"[,\n]", str(labels or "")) if s.strip()]
        return [(n, s, lab if given else "") for (n, lab), s in zip(variants, subs)]
    except ValueError:
        names = [s.strip() for s in re.split(r"[,\n]", str(shortcuts or "")) if s.strip()]
        subs = split_subjects(subjects)
        labs = [s.strip() for s in re.split(r"[,\n]", str(labels or "")) if s.strip()]
        n = max(len(names), len(subs), len(labs), 1)
        pick = lambda xs, i: xs[i] if i < len(xs) else (xs[0] if len(xs) == 1 else "")  # noqa: E731
        return [(pick(names, i), pick(subs, i), labs[i] if i < len(labs) else "") for i in range(n)]


def split_messages(text: str) -> list[str]:
    """API message box -> message texts. A line with only --- separates A/B versions."""
    parts, cur = [], []
    for line in str(text or "").splitlines():
        if re.fullmatch(r"\s*-{3,}\s*", line):
            parts.append("\n".join(cur).strip())
            cur = []
        else:
            cur.append(line)
    parts.append("\n".join(cur).strip())
    return [p for p in parts if p]


def insert_opener(text: str, opener: str) -> str:
    """Put the AI opening line into {ai_opener}; without that placeholder, right after a
    greeting line ("Hi {first_name},") or at the top. No line: the placeholder disappears."""
    opener = str(opener or "").strip()
    if "{ai_opener}" in text:
        if not opener:
            text = re.sub(r"[ \t]*\{ai_opener\}[ \t]*\n?\n?", "", text)
        return text.replace("{ai_opener}", opener)
    if not opener:
        return text
    first, _sep, rest = text.partition("\n")
    if rest and re.match(r"^\s*(hi|hello|hey|dear|greetings|good (morning|afternoon|evening))\b", first, re.I):
        return f"{first}\n\n{opener}\n\n{rest.lstrip(chr(10))}"
    return f"{opener}\n\n{text}"


def api_variants(text: str, versions: str = "") -> list[tuple[str, str]]:
    """Variant names api-1, api-2, ... (one per message text) with their labels."""
    n = max(1, len(split_messages(text)))
    return parse_variants(", ".join(f"api-{i + 1}" for i in range(n)), versions)


def variant_counts(path: Path, campaign: str, variants, subjects=None) -> list[int]:
    """Live sends so far in this campaign per variant (history.csv), to keep the split even.
    With several subjects a send counts for a variant only when its subject matches too."""
    counts = [0] * len(variants)
    campaign = str(campaign or "").strip().lower()
    by_subject = subjects is not None and len(set(subjects)) > 1
    for r in read_history(path):
        if r.get("Mode") != "live" or r.get("Result") not in (SENT, UNCONFIRMED):
            continue
        if str(r.get("Campaign", "")).strip().lower() != campaign:
            continue
        for i, (name, _label) in enumerate(variants):
            if by_subject and str(r.get("Subject", "")).strip() != subjects[i]:
                continue
            if str(r.get("Shortcut", "")).strip() == name:
                counts[i] += 1
    return counts


def pick_variant(counts: list[int]) -> int:
    """Index of the variant with the fewest sends (first one on a tie)."""
    return min(range(len(counts)), key=lambda i: (counts[i], i))

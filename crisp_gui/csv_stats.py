"""Read the recipient CSV the same way crisp_email_agent.py does."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_COLUMNS = ("Full Name", "Contact Email", "Reference", "Mail Sent")

# Must match is_checked() in crisp_email_agent.py (a test enforces this).
SENT_VALUES = {"true", "1", "yes", "y", "checked", "sent", "x", "✓", "☑"}


def _clean(value) -> str:
    return str(value or "").strip()


def is_checked(value) -> bool:
    return _clean(value).lower() in SENT_VALUES


@dataclass
class CsvStats:
    path: Path | None = None
    ok: bool = False
    error: str = ""
    total: int = 0          # data rows in the CSV
    eligible: int = 0       # rows the agent would process now
    marked_sent: int = 0    # rows with Mail Sent checked
    other_reference: int = 0
    missing_email: int = 0
    missing_columns: list = field(default_factory=list)
    # row range (serial = position among data rows, header excluded, from 1)
    range_set: bool = False
    range_from: int = 0
    range_to: int = 0
    in_range: int = 0        # rows inside the range
    duplicates: int = 0      # eligible rows whose email appeared earlier in the range
    range_error: str = ""
    range_error_field: str = ""   # "from" or "to"

    def preview(self) -> str:
        """One line for the UI, e.g. 'Rows 200-300: 101 rows, 85 eligible, 3 duplicates, 4 already sent'."""
        parts = [f"{self.in_range} rows", f"{self.eligible} eligible"]
        if self.duplicates:
            parts.append(f"{self.duplicates} duplicate{'s' if self.duplicates != 1 else ''}")
        parts.append(f"{self.marked_sent} already sent")
        if self.other_reference:
            parts.append(f"{self.other_reference} other reference")
        if self.missing_email:
            parts.append(f"{self.missing_email} without email")
        head = f"Rows {self.range_from}–{self.range_to} of {self.total}: " if self.range_set else ""
        return head + ", ".join(parts)


def parse_range(from_text, to_text, total: int):
    """Blank = all rows. Returns (start, end, error, field). Must match row_range() in the agent."""
    f, t = _clean(from_text), _clean(to_text)
    if not f and not t:
        return 1, total, "", ""
    try:
        start = int(f) if f else 1
    except ValueError:
        return 0, 0, "Enter a whole number.", "from"
    try:
        end = int(t) if t else total
    except ValueError:
        return 0, 0, "Enter a whole number.", "to"
    if start < 1:
        return 0, 0, "From row must be 1 or more.", "from"
    if start > total:
        return 0, 0, f"The file has only {total} rows.", "from"
    if end < 1:
        return 0, 0, "To row must be 1 or more.", "to"
    if end > total:
        return 0, 0, f"The file has only {total} rows.", "to"
    if start > end:
        return 0, 0, "From row must not be after To row.", "from"
    return start, end, "", ""


def read_stats(path_text: str, reference_filter: str, row_from: str = "", row_to: str = "") -> CsvStats:
    stats = CsvStats()
    if not path_text or not path_text.strip():
        stats.error = "Choose a recipient CSV file."
        return stats
    path = Path(path_text.strip()).expanduser()
    stats.path = path
    if not path.is_file():
        stats.error = "File not found."
        return stats
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = list(reader.fieldnames or [])
            rows = list(reader)
    except UnicodeDecodeError:
        stats.error = "The file is not UTF-8 text. Save it as CSV UTF-8."
        return stats
    except OSError as exc:
        stats.error = f"Could not read the file: {exc.strerror or exc}"
        return stats
    except csv.Error as exc:
        stats.error = f"Invalid CSV: {exc}"
        return stats

    if not headers:
        stats.error = "The CSV has no header row."
        return stats
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        stats.missing_columns = missing
        stats.error = "Missing columns: " + ", ".join(missing)
        return stats

    ref = _clean(reference_filter).lower()
    stats.total = len(rows)
    start, end, err, which = parse_range(row_from, row_to, stats.total)
    stats.range_set = bool(_clean(row_from) or _clean(row_to))
    if err:
        stats.range_error, stats.range_error_field = err, which
        stats.ok = True   # the file itself is fine; the range blocks the run
        return stats
    stats.range_from, stats.range_to = start, end
    stats.in_range = max(0, end - start + 1)
    seen = set()
    for row in rows[start - 1:end]:
        sent = is_checked(row.get("Mail Sent"))
        if sent:
            stats.marked_sent += 1
        if _clean(row.get("Reference")).lower() != ref:
            stats.other_reference += 1
            continue
        if not _clean(row.get("Contact Email")):
            stats.missing_email += 1
            continue
        if sent:
            continue
        stats.eligible += 1
        email = _clean(row.get("Contact Email")).lower()
        if email in seen:
            stats.duplicates += 1
        seen.add(email)
    stats.ok = True
    return stats

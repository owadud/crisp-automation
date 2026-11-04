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


def read_stats(path_text: str, reference_filter: str) -> CsvStats:
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
    for row in rows:
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
    stats.ok = True
    return stats

"""Read the recipient CSV the same way crisp_email_agent.py does."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import recipient_rules as rules  # noqa: E402  (shared with the agent)
import send_control as sc  # noqa: E402
from recipient_rules import SENT_VALUES, is_checked  # noqa: E402,F401

# Only the email column is required now (any of: Contact Email, Email, E-mail).
REQUIRED_COLUMNS = ("Contact Email",)


def _clean(value) -> str:
    return str(value or "").strip()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


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
    missing_optional: list = field(default_factory=list)   # Full Name / Reference / Mail Sent
    email_column: str = ""
    # row range (serial = position among data rows, header excluded, from 1)
    range_set: bool = False
    range_from: int = 0
    range_to: int = 0
    in_range: int = 0        # rows inside the range
    valid_emails: int = 0    # rows in range with a well-formed email
    duplicates: int = 0      # skipped: email already sent or earlier in this run
    invalid: int = 0         # skipped: bad email format
    do_not_contact: int = 0  # skipped: on the do-not-contact list
    campaign_sent: int = 0   # skipped: already sent in this campaign (history)
    recent: int = 0          # skipped: emailed in the last N days (history)
    recent_days: int = 0
    claimed: int = 0         # shared Sheet: held by other runners, or waiting for a check in Crisp
    shared: bool = False     # numbers include the shared Sheet log
    invalid_rows: list = field(default_factory=list)   # (serial, email, reason)
    recent_rows: list = field(default_factory=list)    # (serial, email, detail)
    plan: list = field(default_factory=list)           # per-row preview (dicts)
    range_error: str = ""
    range_error_field: str = ""   # "from" or "to"
    dnc_error: str = ""
    retry_candidates: int = 0   # rows "Retry failed only" would send

    def preview(self) -> str:
        """'Found 120 rows · 118 valid emails · 2 invalid · 3 duplicates skipped · ...'."""
        parts = [f"Found {_plural(self.in_range, 'row')}", f"{self.valid_emails} valid email"
                 f"{'' if self.valid_emails == 1 else 's'}"]
        if self.invalid:
            parts.append(f"{self.invalid} invalid")
        if self.duplicates:
            parts.append(f"{_plural(self.duplicates, 'duplicate')} skipped")
        if self.recent:
            parts.append(f"{self.recent} skipped: emailed in the last {self.recent_days} days")
        if self.campaign_sent:
            parts.append(f"{self.campaign_sent} already sent in this campaign")
        if self.claimed:
            parts.append(f"{self.claimed} claimed by other runners")
        if self.do_not_contact:
            parts.append(f"{self.do_not_contact} do-not-contact")
        if self.marked_sent:
            parts.append(f"{self.marked_sent} already sent")
        if self.other_reference:
            parts.append(f"{self.other_reference} other reference")
        if self.missing_email:
            parts.append(f"{self.missing_email} without email")
        parts.append(f"{self.eligible} free for you to send" if self.shared else f"{self.eligible} to send")
        if self.missing_optional:
            parts.append("optional columns missing: " + ", ".join(self.missing_optional))
        head = f"Rows {self.range_from}–{self.range_to} of {self.total}: " if self.range_set else ""
        return head + " · ".join(parts)


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


def read_stats(path_text: str, reference_filter: str, row_from: str = "", row_to: str = "",
               dnc_path: str = "", history_file: str = "", campaign: str = "", recent_days=0,
               shared_file: str = "", claim_expiry=5) -> CsvStats:
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
    colmap = rules.map_columns(headers)
    if "Contact Email" not in colmap:
        stats.missing_columns = ["Contact Email"]
        stats.error = rules.NO_EMAIL_COLUMN
        return stats
    stats.email_column = colmap["Contact Email"]
    stats.missing_optional = rules.missing_optional(colmap)
    rules.apply_aliases(rows, colmap)

    stats.total = len(rows)
    start, end, err, which = parse_range(row_from, row_to, stats.total)
    stats.range_set = bool(_clean(row_from) or _clean(row_to))
    if err:
        stats.range_error, stats.range_error_field = err, which
        stats.ok = True   # the file itself is fine; the range blocks the run
        return stats
    stats.range_from, stats.range_to = start, end
    stats.in_range = max(0, end - start + 1)

    dnc = set()
    if _clean(dnc_path):
        try:
            dnc = rules.load_do_not_contact(dnc_path)
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            stats.dnc_error = f"Do-not-contact list could not be read: {getattr(exc, 'strerror', None) or exc}"

    try:
        stats.recent_days = max(0, int(_clean(recent_days) or 0))
    except ValueError:
        stats.recent_days = 0
    history = (sc.HistoryIndex(Path(_clean(history_file) or "no-history.csv"), campaign, stats.recent_days)
               if _clean(history_file) or _clean(shared_file) else None)
    if _clean(shared_file) and Path(shared_file).is_file():
        # Shared Sheet: sends and claims by every runner (read at "Test connection").
        import json
        import claims
        try:
            raw = json.loads(Path(shared_file).read_text(encoding="utf-8"))
            snap = claims.snapshot_from_raw(raw, campaign, int(_clean(claim_expiry) or 5), stats.recent_days)
            for email, at, camp, result in snap.history():
                history.add(email, at, camp, result)
            history.blocker = lambda email: snap.blocker(email)
            stats.shared = True
        except (OSError, ValueError, TypeError) as exc:
            stats.dnc_error = stats.dnc_error or f"Could not read who else is sending: {exc}"

    counters = {rules.ELIGIBLE: "eligible", rules.OTHER_REFERENCE: "other_reference",
                rules.NO_EMAIL: "missing_email", rules.DUPLICATE: "duplicates",
                rules.INVALID: "invalid", rules.DO_NOT_CONTACT: "do_not_contact",
                rules.CAMPAIGN_SENT: "campaign_sent", rules.RECENT: "recent", rules.CLAIMED: "claimed"}
    for index, row, outcome, detail in rules.classify(rows, reference_filter, start, end, dnc, history=history):
        if outcome == rules.OUT_OF_RANGE:
            continue
        email = _clean(row.get("Contact Email"))
        if is_checked(row.get("Mail Sent")):
            stats.marked_sent += 1
        if email and not rules.email_problem(email):
            stats.valid_emails += 1
        if outcome in counters:
            setattr(stats, counters[outcome], getattr(stats, counters[outcome]) + 1)
        if outcome == rules.INVALID:
            stats.invalid_rows.append((index + 1, email, detail))
        if outcome == rules.RECENT:
            stats.recent_rows.append((index + 1, email, detail))
        name, fallback = rules.display_name(row) if email else ("", False)
        stats.plan.append({"row": index + 1, "email": email, "name": name, "fallback": fallback,
                           "reference": _clean(row.get("Reference")) or _clean(reference_filter),
                           "action": rules.ACTIONS.get(outcome, outcome), "detail": detail,
                           "send": outcome == rules.ELIGIBLE})
    stats.retry_candidates = sum(1 for _i, _r, outcome, _d in
                                 rules.classify(rows, reference_filter, start, end, dnc, retry_only=True,
                                                history=history)
                                 if outcome == rules.ELIGIBLE)
    stats.ok = True
    return stats

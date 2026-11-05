"""Who gets an email. Shared by crisp_email_agent.py and the GUI preview,
so the numbers the GUI shows are exactly what the agent sends.

No third-party imports: the agent runs this as a plain script dependency.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

# Must match is_checked() in crisp_email_agent.py (a test enforces this).
SENT_VALUES = {"true", "1", "yes", "y", "checked", "sent", "x", "✓", "☑"}

# Row outcomes before any browser work.
ELIGIBLE = "eligible"
ALREADY_SENT = "already_sent"
OTHER_REFERENCE = "other_reference"
NO_EMAIL = "no_email"
DUPLICATE = "duplicate"
INVALID = "invalid"
DO_NOT_CONTACT = "do_not_contact"
OUT_OF_RANGE = "out_of_range"
NOT_RETRY = "not_retry"          # "Retry failed only": row did not fail last time

# Status values that "Retry failed only" picks up. Unconfirmed is NOT retried:
# that email may already have gone out.
RETRY_STATUSES = {"failed", "skipped"}

_EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")

# Common misspellings of big mail providers -> the likely intended domain.
TYPO_DOMAINS = {
    "gmial.com": "gmail.com", "gmal.com": "gmail.com", "gmai.com": "gmail.com",
    "gnail.com": "gmail.com", "gamil.com": "gmail.com", "gmaill.com": "gmail.com",
    "gmail.co": "gmail.com", "gmail.cm": "gmail.com", "gmail.con": "gmail.com",
    "gmail.om": "gmail.com", "gmali.com": "gmail.com", "gmil.com": "gmail.com",
    "hotmial.com": "hotmail.com", "hotmal.com": "hotmail.com", "hotmai.com": "hotmail.com",
    "hotmail.co": "hotmail.com", "hotmail.con": "hotmail.com",
    "yahooo.com": "yahoo.com", "yaho.com": "yahoo.com", "yahoo.con": "yahoo.com",
    "outlok.com": "outlook.com", "outllok.com": "outlook.com", "outlook.con": "outlook.com",
    "iclod.com": "icloud.com", "icloud.co": "icloud.com",
}
BAD_TLDS = {"con", "cmo", "comm", "cpm", "vom", "xom"}


def clean(value) -> str:
    return str(value or "").strip()


def normalize_email(value) -> str:
    return clean(value).lower()


def is_checked(value) -> bool:
    return clean(value).lower() in SENT_VALUES


def email_problem(email: str) -> str:
    """'' if the address looks valid, otherwise a short reason."""
    e = clean(email)
    if not _EMAIL_RE.match(e) or ".." in e:
        return "not a valid email address"
    domain = e.rsplit("@", 1)[1].lower()
    if domain in TYPO_DOMAINS:
        return f"looks like a typo of {TYPO_DOMAINS[domain]}"
    if domain.rsplit(".", 1)[1] in BAD_TLDS:
        return f"domain ending .{domain.rsplit('.', 1)[1]} looks like a typo"
    return ""


def load_do_not_contact(path_text: str) -> set[str]:
    """Emails from any cell containing '@' in a CSV (or one-per-line text file).

    Raises OSError/UnicodeDecodeError/csv.Error for the caller to report.
    """
    path = Path(clean(path_text)).expanduser()
    found = set()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            for cell in row:
                if "@" in cell:
                    found.add(normalize_email(cell))
    return found


# ------------------------------------------------------------------ columns
# Only the email column is required. Headers match ignoring case, spaces,
# "-" and "_": "Contact Email", "Email", "email", "E-mail" all work.
CANONICAL = {
    "Contact Email": ("contactemail", "email", "emailaddress"),
    "Full Name": ("fullname", "name"),
    "Reference": ("reference", "ref"),
    "Mail Sent": ("mailsent",),
    "Sent At": ("sentat",),
    "Status": ("status",),
    "Error": ("error",),
    "Conversation link": ("conversationlink",),
}
OPTIONAL_COLUMNS = ("Full Name", "Reference", "Mail Sent")
NO_EMAIL_COLUMN = "No email column found. Name a column Email or Contact Email."


def header_key(header) -> str:
    return re.sub(r"[\s_\-]+", "", str(header or "").strip().lower())


def map_columns(headers) -> dict:
    """{canonical name: the user's own header} for the columns that exist."""
    keyed = [(header_key(h), h) for h in headers if str(h or "").strip()]
    colmap = {}
    for canon, keys in CANONICAL.items():
        for key in keys:                      # priority order: "contactemail" before "email"
            hit = next((h for k, h in keyed if k == key), None)
            if hit is not None:
                colmap[canon] = hit
                break
    return colmap


def missing_optional(colmap) -> list[str]:
    return [c for c in OPTIONAL_COLUMNS if c not in colmap]


def apply_aliases(rows, colmap) -> None:
    """Give every row the canonical keys (values from the user's own columns).

    Writers only write the user's headers, so the file keeps its own names."""
    for row in rows:
        for canon in CANONICAL:
            actual = colmap.get(canon)
            if actual and actual != canon:
                row[canon] = row.get(actual, "")
            elif not actual:
                row.setdefault(canon, "")


def set_cell(row, colmap, canon, value) -> None:
    row[canon] = value
    actual = colmap.get(canon)
    if actual:
        row[actual] = value


def add_column(fieldnames, colmap, canon) -> bool:
    """Append a missing column (named canonically). True if it was added."""
    if canon in colmap:
        return False
    fieldnames.append(canon)
    colmap[canon] = canon
    return True


# ------------------------------------------------------------------ names
_BAD_NAMES = {"", "nan", "none", "null", "n/a", "na", "-", "--", "?", "unknown"}


def fallback_name(email: str) -> str:
    """'john.doe+vip@x.com' -> 'John Doe'. Never blank."""
    local = clean(email).split("@")[0]
    local = re.sub(r"\+.*$", "", local)
    words = [re.sub(r"\d+", "", w) for w in re.split(r"[._\-]+", local)]
    name = " ".join(w.capitalize() for w in words if w)
    return name or "Customer"


def display_name(row) -> tuple[str, bool]:
    """(name to type into Crisp, True if it is a fallback from the email)."""
    name = re.sub(r"\s+", " ", clean(row.get("Full Name")))
    if name.lower() in _BAD_NAMES or not re.search(r"[^\W\d_]", name):
        return fallback_name(row.get("Contact Email")), True
    return name, False


# ------------------------------------------------------------------ who gets an email
CAMPAIGN_SENT = "campaign_sent"   # already sent in this campaign (history)
RECENT = "recent"                 # emailed in the last N days (any campaign)

ACTIONS = {
    ELIGIBLE: "Send",
    ALREADY_SENT: "Skip: already sent",
    OTHER_REFERENCE: "Skip: other reference",
    NO_EMAIL: "Skip: no email",
    DUPLICATE: "Skip: duplicate",
    INVALID: "Skip: invalid",
    DO_NOT_CONTACT: "Skip: do-not-contact",
    CAMPAIGN_SENT: "Skip: already sent in this campaign",
    RECENT: "Skip: emailed recently",
    NOT_RETRY: "Skip: not failed last time",
}


def classify(rows, reference_filter: str, start: int, end: int, do_not_contact=frozenset(),
             retry_only: bool = False, history=None):
    """[(index, row, outcome, detail)] for every row, in file order.

    start/end are 1-based serials (inclusive). A duplicate is an email that
    already appears on a row marked sent anywhere in the file, or on an
    earlier eligible row of this run. A row with an EMPTY Reference belongs
    to whoever sends it. `history` (send_control.HistoryIndex) adds "already
    sent in this campaign" and "emailed in the last N days". Only ELIGIBLE
    rows are sent.
    """
    ref = clean(reference_filter).lower()
    already = {normalize_email(r.get("Contact Email")) for r in rows if is_checked(r.get("Mail Sent"))}
    already.discard("")
    seen = set()
    out = []
    for index, row in enumerate(rows):
        serial = index + 1
        email = normalize_email(row.get("Contact Email"))
        row_ref = clean(row.get("Reference")).lower()
        if not start <= serial <= end:
            out.append((index, row, OUT_OF_RANGE, ""))
        elif row_ref and row_ref != ref:
            out.append((index, row, OTHER_REFERENCE, ""))
        elif not email:
            out.append((index, row, NO_EMAIL, ""))
        elif is_checked(row.get("Mail Sent")):
            out.append((index, row, ALREADY_SENT, ""))
        elif email in do_not_contact:
            out.append((index, row, DO_NOT_CONTACT, ""))
        elif email_problem(email):
            out.append((index, row, INVALID, email_problem(email)))
        elif email in already or email in seen:
            out.append((index, row, DUPLICATE, ""))
        elif history is not None and history.campaign_sent(email):
            t, campaign, result = history.campaign_sent(email)
            out.append((index, row, CAMPAIGN_SENT, f"{result} {t} in '{campaign}'"))
        elif history is not None and history.recent(email):
            t, campaign, result = history.recent(email)
            note = "possibly sent (unconfirmed)" if result.lower() == "unconfirmed" else "sent"
            out.append((index, row, RECENT, f"{note} {t} in '{campaign or '?'}'"))
        elif retry_only and clean(row.get("Status")).lower() not in RETRY_STATUSES:
            out.append((index, row, NOT_RETRY, ""))
        else:
            seen.add(email)
            out.append((index, row, ELIGIBLE, ""))
    return out

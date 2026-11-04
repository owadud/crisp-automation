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


def classify(rows, reference_filter: str, start: int, end: int, do_not_contact=frozenset()):
    """[(index, row, outcome, detail)] for every row, in file order.

    start/end are 1-based serials (inclusive). A duplicate is an email that
    already appears on a row marked sent anywhere in the file, or on an
    earlier eligible row of this run. Only ELIGIBLE rows are sent.
    """
    ref = clean(reference_filter).lower()
    already = {normalize_email(r.get("Contact Email")) for r in rows if is_checked(r.get("Mail Sent"))}
    already.discard("")
    seen = set()
    out = []
    for index, row in enumerate(rows):
        serial = index + 1
        email = normalize_email(row.get("Contact Email"))
        if not start <= serial <= end:
            out.append((index, row, OUT_OF_RANGE, ""))
        elif clean(row.get("Reference")).lower() != ref:
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
        else:
            seen.add(email)
            out.append((index, row, ELIGIBLE, ""))
    return out

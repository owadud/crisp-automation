#!/usr/bin/env python3

"""
Crisp Email Agent
=================

Ubuntu + Playwright UI automation for Crisp.

Workflow:
CSV
 -> New email conversation
 -> fill recipient/name/subject
 -> insert existing Crisp shortcut
 -> send
 -> add pending-cart segment
 -> resolve conversation
 -> mark Mail Sent=TRUE
 -> next recipient

CSV columns:
Full Name, Contact Email, Reference, Mail Sent

Only rows with:
- Reference == "Shahriar"
- Contact Email present
- Mail Sent not already checked

DRY_RUN is intentionally FALSE because real sending is required.
"""

import atexit
import csv
import json
import os
import re
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recipient_rules as rules  # noqa: E402  (same rules as the GUI preview)
import send_control as sc  # noqa: E402  (limits, Dhaka time, history, run folders)
import claims  # noqa: E402  (per-recipient claims on a shared Google Sheet)
import pacing  # noqa: E402  (app-wide send pace across campaigns)
import crisp_api  # noqa: E402  (Crisp REST API: optional sending without a browser)


# ============================================================
# CONFIG
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

CSV_FILE = Path(os.environ.get("CRISP_CSV_FILE", str(BASE_DIR / "wowinvoice_users.csv"))).expanduser().resolve()

SHORTCUT = os.environ.get("CRISP_SHORTCUT", "recommend-offer")

SUBJECT = os.environ.get("CRISP_SUBJECT", "[Action Required] Exclusive VIP Access👀")

REFERENCE_FILTER = os.environ.get("CRISP_REFERENCE_FILTER", "Shahriar")

# Segment to add after sending
SEGMENT_NAME = os.environ.get("CRISP_SEGMENT", "pending-cart")

# Keep FALSE: actual email sending.
DRY_RUN = os.environ.get("CRISP_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}

# Optional row range. Serial = position among data rows (header excluded,
# first data row = 1), counted BEFORE any filtering. Blank = all rows.
ROW_FROM = os.environ.get("CRISP_ROW_FROM", "").strip()
ROW_TO = os.environ.get("CRISP_ROW_TO", "").strip()

# Optional CSV of emails that must never be contacted.
DNC_FILE = os.environ.get("CRISP_DNC_FILE", "").strip()

# Default: stop at the first failure. Opt-in: skip a recipient that failed
# BEFORE the Send click and continue (never after Send: that could resend).
SKIP_FAILED = os.environ.get("CRISP_SKIP_FAILED", "false").lower() in {"1", "true", "yes", "on"}
MAX_SKIPS = int(os.environ.get("CRISP_MAX_SKIPS", "5") or 5)
MAX_FAILS_IN_ROW = int(os.environ.get("CRISP_MAX_FAILS_IN_ROW", "3") or 3)

# Set right before Send is clicked for the current recipient.
SEND_CLICKED = False

PROFILE_DIR = Path(os.environ.get("CRISP_PROFILE_DIR", str(Path.home() / ".crisp_email_agent" / "browser_profile"))).expanduser().resolve()

CRISP_URL = "https://app.crisp.chat/"

ELEMENT_TIMEOUT = 15000

CREATE_WAIT = 2.5

SHORTCUT_WAIT = 1.5

SEND_WAIT = 4

BETWEEN_EMAILS = 3

# Sending control (all optional; blank = off).
DELAY_ENV = os.environ.get("CRISP_DELAY", "").strip()           # seconds between emails
RANDOM_DELAY = os.environ.get("CRISP_RANDOM_DELAY", "false").lower() in {"1", "true", "yes", "on"}
MAX_PER_RUN = os.environ.get("CRISP_MAX_PER_RUN", "").strip()
DAILY_CAP = os.environ.get("CRISP_DAILY_CAP", "").strip()       # real sends per Dhaka day
SEND_WINDOW = os.environ.get("CRISP_SEND_WINDOW", "").strip()   # "10:00-18:00", Dhaka time
START_AT = os.environ.get("CRISP_START_AT", "").strip()         # "10:00", Dhaka time
RETRY_ONLY = os.environ.get("CRISP_RETRY_ONLY", "false").lower() in {"1", "true", "yes", "on"}

# Records.
RUN_DIR = Path(os.environ["CRISP_RUN_DIR"]) if os.environ.get("CRISP_RUN_DIR", "").strip() else None
HISTORY_FILE = Path(os.environ.get("CRISP_HISTORY_FILE", "").strip() or str(BASE_DIR / "history.csv"))
DEBUG_DIR = RUN_DIR or BASE_DIR
EXTRA_COLUMNS = ["Sent At", "Status", "Error", "Conversation link"]
REPORT = []   # one dict per processed recipient, written to RUN_DIR/report.csv

# Source: "csv" (default) or "sheet" (Google Sheet, see sheet_source.py).
SOURCE = os.environ.get("CRISP_SOURCE", "csv").strip().lower() or "csv"
SHEET_URL = os.environ.get("CRISP_SHEET_URL", "").strip()
SHEET_TAB = os.environ.get("CRISP_SHEET_TAB", "").strip()
GOOGLE_KEY = os.environ.get("CRISP_GOOGLE_KEY", "").strip()
DATA_DIR = Path(os.environ.get("CRISP_DATA_DIR", "").strip() or str(BASE_DIR))
SHEET = None         # SheetSource while a Sheet run is active

# Shared Sheet: several runners at once, each recipient claimed separately (claims.py).
RUNNER_NAME = os.environ.get("CRISP_RUNNER_NAME", "").strip()
try:
    CLAIM_BATCH = max(1, int(os.environ.get("CRISP_CLAIM_BATCH", "") or claims.DEFAULT_BATCH))
except ValueError:
    CLAIM_BATCH = claims.DEFAULT_BATCH
try:
    CLAIM_EXPIRY = max(1, int(os.environ.get("CRISP_CLAIM_EXPIRY", "") or claims.DEFAULT_EXPIRY_MIN))
except ValueError:
    CLAIM_EXPIRY = claims.DEFAULT_EXPIRY_MIN
CLAIMS = None        # claims.Claims for a live Sheet run

# App-wide pace (set by the GUI): the delay and daily cap cover ALL campaigns.
PACE_FILE = os.environ.get("CRISP_PACE_FILE", "").strip()
PACER = None
# Working-copy name for a Sheet run, so two campaigns on one Sheet each get their own file.
WORKCOPY_TAG = re.sub(r"[^A-Za-z0-9_-]+", "_", os.environ.get("CRISP_WORKCOPY_TAG", "").strip())[:20]
SHARED = None        # claims.Snapshot read at the start (live and dry runs)
CURRENT_EMAIL = ""   # recipient being processed (for the Sending mark)
SEND_LIMIT = None    # shared Sheet: stop after this many sends (max per run / daily cap)

# History rules (history.csv, all campaigns, all files; only real sends count).
CAMPAIGN = os.environ.get("CRISP_CAMPAIGN", "").strip() or SUBJECT
try:
    RECENT_DAYS = max(0, int(os.environ.get("CRISP_RECENT_DAYS", "7").strip() or 0))
except ValueError:
    RECENT_DAYS = 7
_HISTORY_INDEX = None

# Label for the shortcut text in use (Crisp shortcuts have no versions), e.g. "v2".
SHORTCUT_VERSION = os.environ.get("CRISP_SHORTCUT_VERSION", "").strip()

# A/B test: several shortcuts ("!a, !b") with labels ("v1, v2"). Each recipient gets
# the variant with the fewest sends so far in this campaign, so the split stays even.
# Sending: "browser" (Crisp inbox + saved shortcut, the default) or "api" (Crisp REST
# API with the campaign's own message text; no browser). A/B in API mode = several
# message texts; each variant is named "api-1", "api-2", ... with the same labels.
SEND_MODE = "api" if os.environ.get("CRISP_SEND_MODE", "").strip().lower() == "api" else "browser"
API_MODE = SEND_MODE == "api"
try:
    API_TEXTS = [str(t) for t in json.loads(os.environ.get("CRISP_API_MESSAGES", "") or "[]") if str(t).strip()]
except ValueError:
    API_TEXTS = []
API = None            # crisp_api.CrispAPI during an API run
# AI opening lines (generated and reviewed in the app): {email: {"opener": ...}}.
AI_OPENERS_FILE = os.environ.get("CRISP_AI_OPENERS", "").strip()
try:
    AI_OPENERS = json.loads(Path(AI_OPENERS_FILE).read_text(encoding="utf-8")) if AI_OPENERS_FILE else {}
except (OSError, ValueError):
    AI_OPENERS = {}
API_TEXT = ""         # message text for the current recipient
API_SESSION = ""      # conversation created for the current recipient
API_LINK = ""

# Several subjects ("A | B") pair with the shortcuts / messages in order (A/B test).
SUBJECT_TEXT = SUBJECT
try:
    if API_MODE:
        VARIANTS, SUBJECTS = sc.ab_plan(", ".join(f"api-{i + 1}" for i in range(max(1, len(API_TEXTS)))),
                                        SHORTCUT_VERSION, SUBJECT_TEXT)
    else:
        VARIANTS, SUBJECTS = sc.ab_plan(SHORTCUT, SHORTCUT_VERSION, SUBJECT_TEXT)
except ValueError:
    VARIANTS, SUBJECTS = [(SHORTCUT, SHORTCUT_VERSION)], [SUBJECT_TEXT]
SUBJECT = SUBJECTS[0] or SUBJECT_TEXT
VARIANT_COUNTS = None


def choose_variant():
    """Set SHORTCUT / SHORTCUT_VERSION / SUBJECT for the next recipient. Returns its index."""
    global SHORTCUT, SHORTCUT_VERSION, SUBJECT, VARIANT_COUNTS
    if VARIANT_COUNTS is None:
        VARIANT_COUNTS = ([0] * len(VARIANTS) if DRY_RUN
                          else sc.variant_counts(HISTORY_FILE, CAMPAIGN, VARIANTS, SUBJECTS))
    index = sc.pick_variant(VARIANT_COUNTS)
    SHORTCUT, SHORTCUT_VERSION = VARIANTS[index]
    SUBJECT = SUBJECTS[index] or SUBJECT_TEXT
    if API_MODE:
        global API_TEXT
        API_TEXT = API_TEXTS[index % len(API_TEXTS)] if API_TEXTS else ""
    return index


def personalize(text, name):
    """{name} and {first_name} in an API message become the recipient's name."""
    first = (name or "").split()[0] if (name or "").split() else ""
    return str(text).replace("{first_name}", first).replace("{name}", name or "")


def open_api():
    """API mode: the saved Crisp token (Settings > Crisp API), checked with one read."""
    global API
    cfg = crisp_api.load_config(Path(os.environ.get("CRISP_API_CONFIG") or crisp_api.CONFIG))
    if not crisp_api.configured(cfg):
        raise RuntimeError("Sending with the Crisp API needs the API token: Settings > Crisp API.")
    if not API_TEXTS:
        raise RuntimeError("Sending with the Crisp API needs the message text (Campaign > Message).")
    API = crisp_api.CrispAPI(cfg["website_id"], cfg["identifier"], cfg["key"])
    site = API.website()
    print(f"Crisp API connected: {site.get('name') or cfg['website_id']}. No browser is used.")


def api_send(row):
    """One recipient through the Crisp API: new conversation, email / name / subject /
    segment, then the message. 'Sending' is recorded (shared Sheet) right before the
    message is posted; after that point a failure is never repeated."""
    global API_SESSION, API_LINK, SEND_CLICKED
    email = clean(row.get("Contact Email"))
    name, from_email = rules.display_name(row)
    print(f"  Recipient: {email} | {name}" + (" (name from email)" if from_email else "") + " (Crisp API)")
    API_SESSION = API_LINK = ""
    opener = ""
    if AI_OPENERS_FILE:
        opener = str((AI_OPENERS.get(email.strip().lower()) or {}).get("opener", ""))
        if not opener:
            print("  No AI opening line for this recipient; sending without one.")
    text = personalize(sc.insert_opener(API_TEXT, opener), name)
    if not text.strip():
        raise RuntimeError("The message text is empty.")
    if DRY_RUN:
        print(f"  [DRY RUN] Crisp API: would create the conversation and send {len(text)} characters. "
              "Nothing was created or sent.")
        return True
    API_SESSION = API.create_conversation()
    API_LINK = API.link(API_SESSION)
    API.set_meta(API_SESSION, email=email, nickname=name, subject=SUBJECT,
                 segments=[SEGMENT_NAME] if SEGMENT_NAME else None)
    print("  Conversation created; email, name, subject and segment set.")
    before_send_click()   # shared Sheet: record "Sending" and re-check the claim
    print("  Sending through the Crisp API...")
    SEND_CLICKED = True
    try:
        API.send_text(API_SESSION, text, origin="email")
    except crisp_api.CrispAPIError as exc:
        if not exc.uncertain:
            SEND_CLICKED = False           # Crisp refused it: nothing was sent
            raise RuntimeError(f"Crisp did not accept the message: {exc}")
        raise RuntimeError(f"{exc} The email MAY already have been sent.")
    print("  Email sent successfully.", flush=True)
    emit("sent")
    return True

# Campaign Results: read-only status check of saved conversation links.
RESULTS_CHECK = os.environ.get("CRISP_RESULTS_CHECK", "false").lower() in {"1", "true", "yes", "on"}
RESULTS_INPUT = os.environ.get("CRISP_RESULTS_INPUT", "").strip()     # JSON list of {key, email, link}
RESULTS_OUTPUT = os.environ.get("CRISP_RESULTS_OUTPUT", "").strip()   # JSON lines written here
try:
    RESULTS_DELAY = max(1.0, float(os.environ.get("CRISP_RESULTS_DELAY", "4") or 4))
except ValueError:
    RESULTS_DELAY = 4.0

# Pause: the GUI creates this file; checked only BETWEEN recipients.
PAUSE_FILE = os.environ.get("CRISP_PAUSE_FILE", "").strip()

# Columns the user's file really has: {canonical name: their header}.
COLMAP = {}
SHEET_QUEUE = None   # PendingQueue of Sheet writes that failed

# The desktop GUI creates this file to ask for a graceful stop. The agent
# only checks it BETWEEN recipients, so a send is never interrupted halfway.
STOP_FILE = os.environ.get("CRISP_STOP_FILE", "").strip()


HEALTH_CHECK = os.environ.get("CRISP_HEALTH_CHECK", "false").lower() in {"1", "true", "yes", "on"}


def emit(event, **data):
    """Structured line for the GUI: '@@EVENT {json}'. Humans read the text lines."""
    print("@@EVENT " + json.dumps({"event": event, **data}, ensure_ascii=False), flush=True)


def stop_requested():
    return bool(STOP_FILE) and Path(STOP_FILE).exists()


# ============================================================
# CSV
# ============================================================

def clean(value):
    return str(value or "").strip()


def is_checked(value):
    value = clean(value).lower()

    return value in {
        "true",
        "1",
        "yes",
        "y",
        "checked",
        "sent",
        "x",
        "✓",
        "☑",
    }


def load_csv():
    if not CSV_FILE.exists():
        raise FileNotFoundError(
            f"CSV file not found:\n{CSV_FILE}"
        )

    with CSV_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        reader = csv.DictReader(f)

        if not reader.fieldnames:
            raise RuntimeError(
                "CSV has no header."
            )

        fieldnames = list(reader.fieldnames)

        rows = list(reader)

    # Only an email column is required (Contact Email / Email / E-mail, any case).
    global COLMAP
    COLMAP = rules.map_columns(fieldnames)
    if "Contact Email" not in COLMAP:
        raise RuntimeError(rules.NO_EMAIL_COLUMN)
    rules.apply_aliases(rows, COLMAP)
    missing = rules.missing_optional(COLMAP)
    if missing:
        print("Optional columns missing (handled automatically): " + ", ".join(missing))

    return rows, fieldnames


def save_csv(rows, fieldnames):

    temp = CSV_FILE.with_name(
        CSV_FILE.stem + ".tmp.csv"
    )

    with temp.open(
        "w",
        encoding="utf-8-sig",
        newline=""
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore"
        )

        writer.writeheader()
        writer.writerows(rows)

    os.replace(temp, CSV_FILE)


def row_range(total):
    """(start, end) as 1-based serials. Must match parse_range() in crisp_gui/csv_stats.py."""
    if not ROW_FROM and not ROW_TO:
        return 1, total
    try:
        start = int(ROW_FROM) if ROW_FROM else 1
        end = int(ROW_TO) if ROW_TO else total
    except ValueError:
        raise RuntimeError(f"Row range must be whole numbers (got from={ROW_FROM!r}, to={ROW_TO!r}).")
    if not (1 <= start <= end <= total):
        raise RuntimeError(f"Invalid row range {start}-{end}: the CSV has {total} rows.")
    return start, end


def load_dnc():
    if not DNC_FILE:
        return set()
    try:
        return rules.load_do_not_contact(DNC_FILE)
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"Do-not-contact list could not be read: {DNC_FILE} ({exc})")


def history_index():
    global _HISTORY_INDEX
    if _HISTORY_INDEX is None:
        _HISTORY_INDEX = sc.HistoryIndex(HISTORY_FILE, CAMPAIGN, RECENT_DAYS)
        if SHARED is not None:
            # Shared history: sends by every runner on this Sheet count, not only this PC's.
            for email, at, campaign, result in SHARED.history():
                _HISTORY_INDEX.add(email, at, campaign, result)
            run = CLAIMS.run if CLAIMS is not None else ""
            _HISTORY_INDEX.blocker = lambda email: SHARED.blocker(email, run)
    return _HISTORY_INDEX


def load_shared_state():
    """Sheet runs: read the claims log (live runs also join as a runner)."""
    global CLAIMS, SHARED, _HISTORY_INDEX
    if SHEET is None:
        return
    runner = claims.runner_label(RUNNER_NAME)
    if DRY_RUN:
        try:
            SHARED = claims.read_snapshot(SHEET, CAMPAIGN, CLAIM_EXPIRY, RECENT_DAYS)
        except Exception as exc:  # noqa: BLE001  (a dry run still works without it)
            print(f"WARNING: could not read who else is sending from this Sheet: {exc}")
            return
    else:
        CLAIMS = claims.Claims(SHEET, CAMPAIGN, runner=runner, batch=CLAIM_BATCH, expiry_min=CLAIM_EXPIRY,
                               recent_days=RECENT_DAYS)
        SHARED = CLAIMS.snapshot()
    _HISTORY_INDEX = None
    active = [t for t in SHARED.team() if t["state"] in ("Running", "Paused")]
    print(f"Shared Sheet: runner {runner}, batch {CLAIM_BATCH}, claim expiry {CLAIM_EXPIRY} min. "
          f"Other runners active now: {len(active)}.")
    for t in active:
        print(f"  {t['runner']} | campaign '{t['campaign']}' | {t['state']} | sent {t['sent']}, "
              f"holding {t['claimed']}")
    checks = SHARED.needs_check()
    if checks:
        print(f"WARNING: {len(checks)} recipient(s) on this Sheet were possibly sent (Sending/Unconfirmed). "
              "They are never retried automatically. Check them in Crisp:")
        for c in checks[:10]:
            print(f"  {c.email} | {c.state} | {c.runner} | '{c.campaign}' | {c.updated_at}")


def shared_gate(eligible, index, email):
    """Shared Sheet, right before a recipient: own the claim (claiming the next
    small batch if needed) and make sure nobody marked it sent. '' = go ahead."""
    e = rules.normalize_email(email)
    if e not in CLAIMS.mine:
        seen = len(CLAIMS.released_log)
        CLAIMS.claim_batch([clean(r.get("Contact Email")) for _i, r in eligible[index:]])
        for line in CLAIMS.released_log[seen:]:
            print(f"  Claim released: {line}")
            emit("claim_released", detail=line)
        if e not in CLAIMS.mine:
            return "claimed by another runner, or sent by one meanwhile"
    if SHEET.is_marked_sent(email):
        CLAIMS.release(e, "already marked Mail Sent in the Sheet")
        return "already marked Mail Sent in the Sheet"
    return ""


def before_send_click():
    """Called just before the Send click. Shared Sheet: write Sending and re-check
    the claim. Raises claims.ClaimLost (nothing is sent) if it was lost."""
    if CLAIMS is None or DRY_RUN or not CURRENT_EMAIL:
        return
    try:
        CLAIMS.begin_send(CURRENT_EMAIL)
    except claims.ClaimLost:
        raise
    except Exception as exc:  # noqa: BLE001
        raise claims.ClaimLost(f"could not record 'Sending' in the Sheet ({exc})") from exc


def pace_turn():
    """Live runs from the GUI: wait for the app-wide send turn, then check the
    daily cap (exact: every earlier send is already in history). False = stop."""
    global PACER
    if not PACE_FILE or DRY_RUN:
        return True
    if PACER is None:
        PACER = pacing.Pace(PACE_FILE)

    def told(wait):
        if wait >= 5:
            print(f"  Waiting {wait:.0f} s (app-wide pace, shared by all campaigns)...")
        emit("waiting", seconds=round(wait, 1))
    if not PACER.acquire(stop_requested, told):
        return False
    cap = sc.optional_int(DAILY_CAP, "Daily cap")
    if cap:
        done = sc.sent_today(HISTORY_FILE)
        if done >= cap:
            PACER.release(0)
            print(f"Daily cap reached: {done} of {cap} sent today by all campaigns (Dhaka time). Stopping.")
            emit("window_ended", window="daily cap")
            return False
    return True


def pace_done(delay=None):
    """Hand the send turn back; the next email from ANY campaign waits `delay`."""
    if PACER is not None and PACER.held:
        wait = between_delay() if delay is None else delay
        PACER.release(wait)
        return wait
    return None


def claim_result(email, result, note=""):
    """Shared Sheet: record Sent / Failed / Unconfirmed in the claims log.
    If Google cannot be reached, it is queued on disk and written later."""
    if CLAIMS is None or DRY_RUN:
        return
    try:
        CLAIMS.finish(email, result, note)
    except Exception as exc:  # noqa: BLE001
        state = {sc.SENT: claims.SENT, sc.UNCONFIRMED: claims.UNCONFIRMED}.get(result, claims.FAILED)
        SHEET_QUEUE.add_claim(SHEET.id, SHEET.tab, CLAIMS._row(email, state, str(note)[:200]))
        if rules.normalize_email(email) in CLAIMS.mine:
            CLAIMS.mine.remove(rules.normalize_email(email))
        print(f"  WARNING: claim result not written to the Sheet ({exc}). Saved locally; written later.")


def end_claims(note, state):
    """Give back claims that never reached Sending; mark this runner finished."""
    global CLAIMS
    if CLAIMS is None:
        return
    try:
        n = CLAIMS.release_all(note)
        if n:
            print(f"Released {n} claimed recipient(s) that were not sent ({note}).")
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not release claims ({exc}); they expire after {CLAIM_EXPIRY} min.")
    CLAIMS.close(state)
    CLAIMS = None


def classify_rows(rows):
    start, end = row_range(len(rows))
    return rules.classify(rows, REFERENCE_FILTER, start, end, load_dnc(), retry_only=RETRY_ONLY,
                          history=history_index())


def get_eligible(rows):
    return [(index, row) for index, row, outcome, _ in classify_rows(rows) if outcome == rules.ELIGIBLE]


def print_precheck(rows):
    """Report rows skipped before any browser work (never stops the run)."""
    counts = {}
    for index, row, outcome, detail in classify_rows(rows):
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == rules.INVALID:
            print(f"INVALID EMAIL row {index + 1}: {clean(row.get('Contact Email'))} ({detail}). Skipped.")
        if outcome == rules.RECENT:
            print(f"EMAILED RECENTLY row {index + 1}: {clean(row.get('Contact Email'))} ({detail}). Skipped.")
            if "unconfirmed" in detail:
                print(f"WARNING: {clean(row.get('Contact Email'))} was possibly sent before (unconfirmed); "
                      "check Crisp before contacting them again.")
        if outcome == rules.CAMPAIGN_SENT:
            print(f"ALREADY IN CAMPAIGN row {index + 1}: {clean(row.get('Contact Email'))} ({detail}). Skipped.")
    parts = []
    for key, label in ((rules.DUPLICATE, "duplicates"), (rules.INVALID, "invalid emails"),
                       (rules.DO_NOT_CONTACT, "do-not-contact"),
                       (rules.CAMPAIGN_SENT, "already sent in this campaign"),
                       (rules.RECENT, f"emailed in the last {RECENT_DAYS} days"),
                       (rules.CLAIMED, "claimed by other runners or waiting for a check")):
        if counts.get(key):
            parts.append(f"{counts[key]} {label}")
    if parts:
        print("Skipped before start: " + ", ".join(parts))
    if SHARED is not None:
        sent = counts.get(rules.ALREADY_SENT, 0) + counts.get(rules.CAMPAIGN_SENT, 0) + counts.get(rules.RECENT, 0)
        print(f"Shared Sheet: {counts.get(rules.ELIGIBLE, 0)} free for this runner · "
              f"{counts.get(rules.CLAIMED, 0)} claimed by others · {sent} already sent")
        emit("shared_counts", free=counts.get(rules.ELIGIBLE, 0), claimed=counts.get(rules.CLAIMED, 0),
             sent=sent)


# ------------------------------------------------------------ CSV safety
def lock_path():
    return CSV_FILE.with_name(CSV_FILE.name + ".lock")


def _pid_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock():
    """One run per CSV. A lock left by a crashed run on this PC is replaced."""
    path = lock_path()
    me = f"pid={os.getpid()}\nhost={socket.gethostname()}\nstarted={datetime.now():%Y-%m-%d %H:%M:%S}\n"
    for _ in range(2):
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            text = path.read_text(encoding="utf-8")
            info = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
            pid = int(info.get("pid", "0") or 0)
            if info.get("host") == socket.gethostname() and not _pid_alive(pid):
                path.unlink(missing_ok=True)
                print("Removed a stale lock left by a run that stopped unexpectedly.")
                continue
            raise RuntimeError(
                f"This CSV is already in use by another run (host {info.get('host', '?')}, "
                f"started {info.get('started', '?')}). If no other copy is running, delete {path}")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(me)
        atexit.register(release_lock)
        return path
    raise RuntimeError(f"Could not create the lock file {path}")


def release_lock():
    try:
        path = lock_path()
        if path.exists() and f"pid={os.getpid()}" in path.read_text(encoding="utf-8"):
            path.unlink()
    except OSError:
        pass


def open_sheet():
    """Download the Sheet into a local working CSV; the run then uses that file."""
    global CSV_FILE, SHEET, SHEET_QUEUE
    import sheet_source as ss
    problem = ss.key_problem(GOOGLE_KEY, BASE_DIR)
    if problem:
        raise RuntimeError(problem)
    src = ss.SheetSource(SHEET_URL, SHEET_TAB, GOOGLE_KEY).connect()
    queue = ss.PendingQueue(DATA_DIR / "sheet_cache" / "pending.json")
    rows, header = src.download()
    had_pending = bool(queue.for_sheet(src.id, src.tab))
    remaining = queue.flush(src) if had_pending else []
    if had_pending:
        rows, header = src.download()
    # Results still not in the Sheet count as done locally: never re-send them.
    cmap = rules.map_columns(header)
    for item in remaining:
        for row in rows:
            if rules.normalize_email(row.get(cmap.get("Contact Email", "Contact Email"))) == \
                    rules.normalize_email(item["email"]):
                for canon, value in item["values"].items():
                    if canon in cmap:
                        row[cmap[canon]] = value
    safe_tab = re.sub(r"[^A-Za-z0-9_-]+", "_", SHEET_TAB)[:40]
    tag = f"_{WORKCOPY_TAG}" if WORKCOPY_TAG else ""
    local = ss.SheetSource.write_local(DATA_DIR / "sheet_cache" / f"{src.id[:12]}_{safe_tab}{tag}.csv", rows, header)
    CSV_FILE = local.resolve()
    SHEET, SHEET_QUEUE = src, queue
    print(f"Google Sheet: tab '{SHEET_TAB}', {len(rows)} rows. Working copy: {local.name}")
    emit("sheet_loaded", tab=SHEET_TAB, rows=len(rows), pending=len(remaining))
    if remaining:
        print(f"WARNING: {len(remaining)} earlier result(s) are still not written to the Sheet. "
              "They are kept as sent here and retried; they will NOT be sent again.")


def sync_row(rows, row_index, columns=None):
    """Live runs: copy this row's result columns to the Sheet (by email). Never raises.
    `columns` limits what is written, so a stale local copy never overwrites
    Mail Sent written by another runner."""
    if SHEET is None or DRY_RUN:
        return
    row = rows[row_index]
    values = {c: row.get(c, "") for c in (columns or ("Mail Sent", "Reference", *EXTRA_COLUMNS)) if c in COLMAP}
    email = clean(row.get("Contact Email"))
    try:
        SHEET.push(email, values, prefer_row=row_index + 2)
    except Exception as exc:  # noqa: BLE001
        SHEET_QUEUE.add(SHEET.id, SHEET.tab, email, row_index + 2, values)
        print(f"  WARNING: Sheet not updated for {email} ({exc}). Saved locally; retried later. "
              "It will not be sent again.")


def ensure_columns(fieldnames, rows=None):
    """Live runs: add missing columns. A new Mail Sent column starts FALSE on every row."""
    added = []
    wanted = ["Mail Sent"] + (["Reference"] if REFERENCE_FILTER else []) + EXTRA_COLUMNS
    for column in wanted:
        if rules.add_column(fieldnames, COLMAP, column):
            added.append(column)
            if column == "Mail Sent":
                for row in rows or []:
                    row["Mail Sent"] = "FALSE"
    if added:
        print("Added columns: " + ", ".join(added))
    return added


def mark_precheck(rows, fieldnames):
    """Live runs: write Status/Error for rows skipped before the run."""
    changed = False
    changed_rows = []
    labels = {rules.INVALID: sc.INVALID, rules.DUPLICATE: sc.SKIPPED, rules.DO_NOT_CONTACT: sc.SKIPPED,
              rules.RECENT: sc.SKIPPED, rules.CAMPAIGN_SENT: sc.SKIPPED}
    reasons = {rules.DUPLICATE: "duplicate email", rules.DO_NOT_CONTACT: "do-not-contact list"}
    for index, row, outcome, detail in classify_rows(rows):
        if outcome in labels:
            status, error = labels[outcome], detail or reasons.get(outcome, "")
            if row.get("Status") != status or row.get("Error") != error:
                rules.set_cell(row, COLMAP, "Status", status)
                rules.set_cell(row, COLMAP, "Error", error)
                changed = True
                changed_rows.append((index, outcome))
    if changed:
        save_csv(rows, fieldnames)
        for index, outcome in changed_rows:
            # Shared Sheet: history-based skips are this runner's view only; never write them there.
            if SHEET is None or outcome in (rules.INVALID, rules.DUPLICATE, rules.DO_NOT_CONTACT):
                sync_row(rows, index, ("Status", "Error"))


def record(rows, fieldnames, row_index, status, error=""):
    """Live runs only: Status/Error columns for a recipient that was not sent."""
    if DRY_RUN:
        return
    rules.set_cell(rows[row_index], COLMAP, "Status", status)
    rules.set_cell(rows[row_index], COLMAP, "Error", str(error)[:300])
    save_csv(rows, fieldnames)
    sync_row(rows, row_index, ("Status", "Error"))


def log_result(row_index, row, result, error="", link=""):
    entry = {"Row": row_index + 1, "Email": clean(row.get("Contact Email")),
             "Name": rules.display_name(row)[0],
             "Result": result, "Error": str(error)[:300], "Time": sc.stamp(), "Conversation link": link}
    REPORT.append(entry)
    if DRY_RUN:
        return   # a dry run never writes history
    try:
        sc.append_history(HISTORY_FILE, Email=entry["Email"], Name=entry["Name"], Result=result,
                          Campaign=CAMPAIGN, Subject=SUBJECT, Mode="live", Error=entry["Error"],
                          Row=entry["Row"], CSV=CSV_FILE.name, Shortcut=SHORTCUT,
                          Version=SHORTCUT_VERSION, Link=link)
    except OSError as exc:
        print(f"  WARNING: could not write history: {exc}")


def failure_screenshot(page, position, email):
    if API_MODE:
        return
    if not RUN_DIR:
        return
    try:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", email)[:40]
        path = RUN_DIR / f"failure_{position:03d}_{safe}.png"
        page.screenshot(path=str(path))
        print(f"  Screenshot saved: {path.name}")
    except Exception:
        pass


def write_run_files():
    if not RUN_DIR:
        return
    try:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "recipients_after.csv").write_bytes(CSV_FILE.read_bytes())
        with (RUN_DIR / "report.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["Row", "Email", "Name", "Result", "Error", "Time",
                                                        "Conversation link"])
            writer.writeheader()
            writer.writerows(REPORT)
        print(f"Report:    {RUN_DIR / 'report.csv'}")
    except OSError as exc:
        print(f"WARNING: could not write the run report: {exc}")


def between_delay():
    if RANDOM_DELAY:
        return sc.random_delay()
    return float(DELAY_ENV) if DELAY_ENV else BETWEEN_EMAILS


def apply_limits(eligible):
    """Max per run and the daily cap (real sends per Dhaka day, from history.csv)."""
    limit, notes = None, []
    per_run = sc.optional_int(MAX_PER_RUN, "Max emails per run")
    if per_run:
        limit = per_run
        notes.append(f"max {per_run} per run")
    cap = sc.optional_int(DAILY_CAP, "Daily cap")
    if cap and not DRY_RUN:
        done = sc.sent_today(HISTORY_FILE)
        left = max(0, cap - done)
        notes.append(f"daily cap {cap}, {done} sent today")
        limit = left if limit is None else min(limit, left)
        if left == 0:
            print(f"Daily cap reached: {done} of {cap} sent today (Dhaka time). Nothing more is sent today.")
    if limit is not None and len(eligible) > limit:
        print(f"Limit: sending {limit} of {len(eligible)} eligible ({'; '.join(notes)}).")
        if SHEET is not None and not DRY_RUN:
            # Shared Sheet: others may take some recipients, so keep the list and count sends.
            global SEND_LIMIT
            SEND_LIMIT = limit
            return eligible if limit else []
        eligible = eligible[:limit]
    return eligible


def backup_csv():
    """recipients.YYYY-MM-DD-HHMM.bak.csv next to the CSV, before a real run."""
    target = CSV_FILE.with_name(f"{CSV_FILE.stem}.{datetime.now():%Y-%m-%d-%H%M}.bak.csv")
    target.write_bytes(CSV_FILE.read_bytes())
    print(f"CSV backup: {target.name}")
    return target


# ============================================================
# GENERIC PLAYWRIGHT HELPERS
# ============================================================

def visible(locator):

    try:
        return locator.is_visible()

    except Exception:
        return False


def enabled(locator):

    try:
        return locator.is_enabled()

    except Exception:
        return True


def box(locator):

    try:
        return locator.bounding_box()

    except Exception:
        return None


def click_first_visible(locators):

    for locator in locators:

        try:

            count = locator.count()

            for i in range(count):

                item = locator.nth(i)

                if visible(item):

                    item.click()

                    return True

        except Exception:
            continue

    return False


# ============================================================
# CRISP: WAIT / NEW CONVERSATION
# ============================================================

def wait_for_crisp(page, timeout_seconds=300):
    """Wait until the authenticated Crisp Inbox is actually available.

    The agent proceeds automatically after login; no terminal Enter or GUI
    Continue button is required. The inbox route plus its visible navigation
    text are used to avoid starting while the login page is still loading.
    """
    print("Waiting for Crisp Inbox login...")
    emit("login_wait")
    deadline = time.time() + timeout_seconds
    last_notice = 0

    while time.time() < deadline:
        if page.is_closed():
            raise RuntimeError("Crisp browser was closed before the Inbox loaded.")

        try:
            current_url = page.url.lower()
            body_text = page.locator("body").inner_text(timeout=1500).lower()
            inbox_route = "/inbox" in current_url
            inbox_navigation = "inbox" in body_text and "filters" in body_text

            if inbox_route and inbox_navigation:
                # Let the SPA finish rendering the Inbox controls.
                page.wait_for_timeout(1200)
                print("Crisp Inbox detected. Starting automatically.")
                emit("logged_in")
                return
        except Exception:
            pass

        now = time.time()
        if now - last_notice >= 10:
            print("Still waiting for Crisp login / Inbox to finish loading...")
            last_notice = now
        time.sleep(1)

    raise TimeoutError(
        "Crisp Inbox was not detected within 5 minutes. "
        "Complete login and make sure the Inbox page is open."
    )


def wait_for_new_email_dialog(
    page,
    timeout_ms
):

    deadline = (
        time.time()
        + timeout_ms / 1000
    )

    selectors = [
        'input[placeholder*="email of the user" i]',
    ]

    while time.time() < deadline:

        for selector in selectors:

            try:

                locator = page.locator(
                    selector
                )

                for i in range(
                    locator.count()
                ):

                    item = locator.nth(i)

                    if visible(item):
                        return True

            except Exception:
                pass

        time.sleep(0.1)

    return False


def open_new_conversation(page):

    """
    Crisp's current UI has a + beside Filters.

    We first try semantic selectors, then locate the +
    relative to Filters.
    """

    selectors = [
        'button[aria-label*="new" i]',
        'button[aria-label*="create" i]',
        'button[aria-label*="conversation" i]',
        'button[title*="new" i]',
        'button[title*="create" i]',
        'button:has-text("New conversation")',
        'button:has-text("New Conversation")',
    ]

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if not visible(item):
                    continue

                item.click()

                if wait_for_new_email_dialog(
                    page,
                    4000
                ):
                    return

        except Exception:
            continue

    # --------------------------------------------------------
    # Current Crisp layout: Filters + plus
    # --------------------------------------------------------

    try:

        filters = page.get_by_text(
            "Filters",
            exact=True
        ).last

        filters.wait_for(
            state="visible",
            timeout=8000
        )

        filters_box = box(
            filters
        )

        if not filters_box:

            raise RuntimeError(
                "Could not determine Filters position."
            )

        y = (
            filters_box["y"]
            + filters_box["height"] / 2
        )

        start_x = (
            filters_box["x"]
            + filters_box["width"]
            + 10
        )

        for x in range(
            int(start_x),
            int(start_x + 90),
            8
        ):

            page.mouse.click(
                x,
                y
            )

            if wait_for_new_email_dialog(
                page,
                800
            ):
                return

    except Exception:
        pass

    raise RuntimeError(
        "Could not open Crisp's + / New conversation dialog."
    )


def _norm(text):
    """Normalise text for comparing subjects (emoji variation selectors, spaces)."""
    import unicodedata
    text = unicodedata.normalize("NFKC", text or "")
    for ch in ("\ufe0f", "\u200b", "\u200d", "\u00a0"):
        text = text.replace(ch, " " if ch == "\u00a0" else "")
    return re.sub(r"\s+", " ", text).strip().casefold()


def _subject_input(page):
    return page.locator('input[placeholder*="subject of the email" i]').first


def _menu_rows(page):
    """Visible rows of Crisp's custom select menu -> [(locator, text)]."""
    rows = []
    wrappers = page.locator(".c-base-select-menu-item__wrapper")
    for i in range(wrappers.count()):
        w = wrappers.nth(i)
        try:
            if not w.is_visible():
                continue
            label = w.locator(".c-base-select-menu-item__label").first
            text = label.inner_text().strip() if label.count() else w.inner_text().strip()
            rows.append((w, text))
        except Exception:
            continue
    return rows


def _find_subject_row(page):
    """Only a row whose text is EXACTLY the full subject is accepted.

    (A partial match used to accept Crisp's echo of a half-typed subject,
    e.g. '⏳ Final Ho', which then became the email subject.)
    """
    want = _norm(SUBJECT)
    rows = _menu_rows(page)
    for idx, (w, text) in enumerate(rows):
        if _norm(text) == want:
            return idx, w, rows
    return None, None, rows


def _subject_value(field):
    try:
        return field.input_value()
    except Exception:
        return None


def _subject_is_full(field):
    value = _subject_value(field)
    return value is not None and _norm(value) == _norm(SUBJECT)


def _debug_subject(page, tag):
    try:
        shot = DEBUG_DIR / f"debug_{tag}.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception as exc:
        print(f"  Debug screenshot failed: {exc!r}", flush=True)
    try:
        html = page.evaluate(
            """() => {
                const el = document.querySelector('.c-base-select-menu-item__wrapper');
                const root = el ? (el.closest('[class*=select-menu]') || el.parentElement) : null;
                return root ? root.outerHTML.slice(0, 6000) : 'NO MENU ELEMENT FOUND';
            }"""
        )
        out = DEBUG_DIR / f"debug_{tag}.html"
        out.write_text(html, encoding="utf-8")
        print(f"  Debug menu HTML saved: {out}", flush=True)
    except Exception as exc:
        print(f"  Debug HTML dump failed: {exc!r}", flush=True)


def pick_email_suggestion(page, email_field, email):
    """Best-effort: click the matching contact suggestion like a human does."""
    try:
        page.wait_for_timeout(700)
        for w, text in _menu_rows(page):
            if _norm(email) in _norm(text):
                w.click(timeout=2000)
                page.wait_for_timeout(400)
                print("  Email suggestion selected.", flush=True)
                return
    except Exception:
        pass


def select_subject_from_dropdown(page):
    """Replicates the manual flow recorded in subject_probe_report.txt:
    click the EMPTY Subject input -> menu opens -> click the matching row
    (div.c-base-select-menu-item__wrapper). Returns True if the menu closed
    after the click. Enter is only used while the menu is open."""
    field = _subject_input(page)
    full_subject = SUBJECT.strip()

    for attempt in range(1, 5):
        try:
            field.scroll_into_view_if_needed()
            field.fill("")
            field.click()
            page.wait_for_timeout(900)

            idx, row, rows = _find_subject_row(page)
            if row is None and attempt >= 2:
                # Some Crisp builds only list the subject after typing.
                # Type the FULL subject (never a prefix) and wait until the
                # field really contains all of it before looking at the menu.
                field.press_sequentially(full_subject, delay=45)
                for _ in range(20):
                    if _subject_is_full(field):
                        break
                    page.wait_for_timeout(150)
                if not _subject_is_full(field):
                    print(
                        f"  Subject typing incomplete: {_subject_value(field)!r}; retrying.",
                        flush=True,
                    )
                    continue
                page.wait_for_timeout(900)
                idx, row, rows = _find_subject_row(page)

            print(f"  Subject menu rows (attempt {attempt}): {[t for _, t in rows]}", flush=True)
            if row is None:
                continue

            row.scroll_into_view_if_needed()
            b = row.bounding_box()

            # Strategy 1: normal click
            try:
                row.hover(timeout=2000)
                page.wait_for_timeout(200)
                row.click(timeout=2500)
            except Exception as exc:
                print(f"  Row click warning: {exc!r}", flush=True)
            page.wait_for_timeout(700)

            # Strategy 2: raw mouse down/up on the row centre
            if _find_subject_row(page)[1] is not None and b:
                cx, cy = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
                page.mouse.move(cx, cy)
                page.wait_for_timeout(150)
                page.mouse.down()
                page.wait_for_timeout(80)
                page.mouse.up()
                page.wait_for_timeout(700)

            # Strategy 3: keyboard, only while the menu is still open
            if _find_subject_row(page)[1] is not None:
                try:
                    field.focus()
                    for _ in range(idx + 1):
                        page.keyboard.press("ArrowDown")
                        page.wait_for_timeout(120)
                    page.keyboard.press("Enter")
                    page.wait_for_timeout(700)
                except Exception:
                    pass

            if _find_subject_row(page)[1] is None:
                val = _subject_value(field)
                if not _subject_is_full(field):
                    # Menu closed but the field doesn't hold the whole subject.
                    print(
                        f"  Subject field shows {val!r}, not the full subject; retrying.",
                        flush=True,
                    )
                    continue
                print(f"  Subject chosen from dropdown. Input value: {val!r}", flush=True)
                return True
        except Exception as exc:
            print(f"  Subject select attempt {attempt} error: {exc!r}", flush=True)
            page.wait_for_timeout(500)

    _debug_subject(page, "subject_select_failed")
    return False


def dialog_closed(page, timeout_ms=8000):
    try:
        _subject_input(page).wait_for(state="hidden", timeout=timeout_ms)
        return True
    except Exception:
        return False


def click_create_conversation(page):
    create_button = page.get_by_role(
        "button",
        name=re.compile(r"Create Conversation", re.I)
    ).first
    create_button.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)

    for _ in range(40):
        try:
            if create_button.is_enabled():
                break
        except Exception:
            pass
        time.sleep(0.25)
    if not create_button.is_enabled():
        raise RuntimeError("Create Conversation button is disabled.")

    try:
        create_button.click(timeout=4000)
    except Exception:
        bb = create_button.bounding_box()
        if not bb:
            raise
        page.mouse.click(bb["x"] + bb["width"] / 2, bb["y"] + bb["height"] / 2)
    print("  Create Conversation clicked.", flush=True)


def fill_new_conversation(
    page,
    email,
    name
):
    email_field = page.locator(
        'input[placeholder*="email of the user" i]'
    ).first
    name_field = page.locator(
        'input[placeholder*="full name of the user" i]'
    ).first
    subject_field = _subject_input(page)

    email_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
    name_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
    subject_field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)

    email_field.fill(email)
    pick_email_suggestion(page, email_field, email)
    name_field.click()
    name_field.fill(name or email.split("@")[0])

    last_said = []   # what Crisp showed when it refused (for the error message)
    for attempt in range(1, 4):
        chosen = select_subject_from_dropdown(page)
        if not chosen:
            print("  Subject could not be chosen from the dropdown.", flush=True)
            break

        # Last check right before Create: never create a conversation with a
        # partial or different subject.
        if not _subject_is_full(subject_field):
            print(
                f"  Subject changed to {_subject_value(subject_field)!r} before Create; retrying.",
                flush=True,
            )
            continue

        click_create_conversation(page)

        if dialog_closed(page, 8000):
            print("  New-conversation dialog closed; conversation created.", flush=True)
            time.sleep(CREATE_WAIT)
            return

        said = crisp_messages(page)
        print(f"  Dialog still open after Create (attempt {attempt}); retrying."
              + (f" Crisp shows: {' | '.join(said)}" if said else ""), flush=True)
        if said:
            last_said = said

    _debug_subject(page, "create_failed")
    said = crisp_messages(page) or last_said
    raise RuntimeError(
        "Crisp did not create the conversation"
        + (f" (Crisp says: {' | '.join(said)})" if said else "")
        + ". Nothing was sent to this recipient. See debug_create_failed.* in the run folder."
    )


def crisp_messages(page):
    """Read-only: visible error / warning / notice texts on the page (e.g. why Crisp
    refuses to create a conversation). Used only to explain a failure."""
    try:
        texts = page.evaluate(
            """() => {
                const sel = '[role="alert"], [class*="toast"], [class*="notification"], [class*="error"], '
                          + '[class*="invalid"], [class*="warning"], [class*="message-box"]';
                const out = [];
                document.querySelectorAll(sel).forEach(e => {
                    if (!e.offsetParent) return;
                    const t = (e.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (t && t.length <= 240 && !out.includes(t)) out.push(t);
                });
                return out.slice(0, 5);
            }"""
        )
        return [str(t) for t in texts or []]
    except Exception:
        return []


# ============================================================
# CRISP - COMPOSER
# ============================================================

def find_composer(page):

    selectors = [
        'textarea[placeholder*="Send your message" i]',
        'textarea[placeholder*="message" i]',
        '[contenteditable="true"]',
        '[role="textbox"]',
    ]

    candidates = []

    for selector in selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if not visible(item):
                    continue

                item_box = box(
                    item
                )

                if not item_box:
                    continue

                if item_box["y"] < 350:
                    continue

                candidates.append(
                    (
                        item_box["y"],
                        item_box["x"],
                        item
                    )
                )

        except Exception:
            continue

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: item[0],
        reverse=True
    )

    return candidates[0][2]


def get_composer_text(composer):

    if composer is None:
        return ""

    try:

        value = composer.input_value()

        if value is not None:
            return clean(value)

    except Exception:
        pass

    try:

        return clean(
            composer.inner_text()
        )

    except Exception:
        pass

    try:

        return clean(
            composer.text_content()
        )

    except Exception:
        pass

    return ""


def composer_is_empty(page):

    composer = find_composer(
        page
    )

    if not composer:
        return False

    text = get_composer_text(
        composer
    )

    return not text.strip()


# ============================================================
# CRISP - SHORTCUT
# ============================================================

def _debug_dump(page, tag):
    """Save a screenshot and print Shortcut-related elements to help debugging."""
    try:
        shot = DEBUG_DIR / f"debug_{tag}.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception as exc:
        print(f"  Debug screenshot failed: {exc!r}", flush=True)
    try:
        items = page.evaluate(
            """() => [...document.querySelectorAll('button,[role=tab],[role=button],span,div,a')]
              .filter(e => e.children.length <= 2 && /short/i.test((e.innerText||'').trim()) && (e.innerText||'').trim().length < 40)
              .slice(0, 15)
              .map(e => { const r = e.getBoundingClientRect();
                  return {tag: e.tagName, text: e.innerText.trim(), cls: (e.className||'').toString().slice(0,60),
                          x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; })"""
        )
        print(f"  Elements containing 'short': {items}", flush=True)
    except Exception as exc:
        print(f"  Element dump failed: {exc!r}", flush=True)


def open_shortcut_picker(page):
    """Open Crisp's Shortcuts tab (never Ctrl+K, never Escape).

    The old working version clicked the lowest visible 'Shortcuts' text.
    This version does the same, but first waits for the Create dialog to close
    and for the composer to appear, and polls for the tab instead of checking once.
    """

    # 1) Wait for the 'New conversation' dialog to close.
    try:
        page.locator('input[placeholder*="subject of the email" i]').first.wait_for(
            state="hidden", timeout=10000
        )
    except Exception:
        print("  Warning: new-conversation dialog still looks open.", flush=True)

    # 2) Wait for the conversation composer to be present.
    deadline = time.time() + 12
    while time.time() < deadline:
        try:
            if find_composer(page):
                break
        except Exception:
            pass
        time.sleep(0.4)

    # 3) Poll for a visible 'Shortcuts' element; click the lowest one on screen.
    deadline = time.time() + 12
    while time.time() < deadline:
        found = []
        locators = [
            page.get_by_text("Shortcuts", exact=True),
            page.get_by_text(re.compile(r"^\s*Shortcuts?\s*$", re.I)),
            page.get_by_role("button", name=re.compile(r"shortcuts?", re.I)),
            page.get_by_role("tab", name=re.compile(r"shortcuts?", re.I)),
        ]
        for loc in locators:
            try:
                for i in range(loc.count()):
                    item = loc.nth(i)
                    if not visible(item):
                        continue
                    item_box = box(item)
                    if item_box and item_box["width"] > 0 and item_box["height"] > 0:
                        found.append((item_box["y"], item, item_box))
            except Exception:
                continue

        if found:
            found.sort(key=lambda x: x[0], reverse=True)
            _, item, item_box = found[0]
            try:
                item.click(timeout=3000)
            except Exception:
                page.mouse.click(
                    item_box["x"] + item_box["width"] / 2,
                    item_box["y"] + item_box["height"] / 2,
                )
            page.wait_for_timeout(700)
            print("  Crisp Shortcuts tab opened.", flush=True)
            return
        time.sleep(0.5)

    _debug_dump(page, "shortcuts_tab_missing")
    raise RuntimeError(
        "Could not locate the visible 'Shortcuts' tab in Crisp "
        "(see debug_shortcuts_tab_missing.png in the project folder)."
    )


def shortcut_content_present(page):
    """True only when the COMPOSER holds the expanded shortcut text.

    The typed keyword (e.g. 'bf-offer-3') must be gone from the composer.
    The page body is not checked: the open Shortcuts list and other chats
    can contain words like 'Best regards', which gave false positives.
    """

    text = get_composer_text(find_composer(page))
    if not text or SHORTCUT in text:
        return False
    return len(text) > 20


def _shortcut_row(page):
    """The visible '!<shortcut>' row in Crisp's Shortcuts list, or None."""
    pattern = re.compile(r"^\s*!" + re.escape(SHORTCUT) + r"\s*$", re.I)
    try:
        rows = page.get_by_text(pattern)
        for i in range(rows.count()):
            row = rows.nth(i)
            if visible(row):
                return row
    except Exception:
        pass
    return None


def _wait_shortcut_inserted(page, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if shortcut_content_present(page):
            return True
        time.sleep(0.25)
    return False


def insert_shortcut(page):

    print(
        f"  Opening shortcut picker: {SHORTCUT}"
    )

    open_shortcut_picker(
        page
    )

    composer = find_composer(
        page
    )

    if not composer:

        raise RuntimeError(
            "Could not find Crisp message composer."
        )

    composer.click()

    print(
        "  Typing shortcut..."
    )

    page.keyboard.type(
        SHORTCUT,
        delay=35
    )

    time.sleep(
        SHORTCUT_WAIT
    )

    print(
        "  Selecting shortcut..."
    )

    # --------------------------------------------------------
    # Wait until Crisp's list shows '!<shortcut>' before pressing
    # Enter. On a slow connection Enter used to arrive before the list
    # was ready and the shortcut was never expanded.
    # --------------------------------------------------------

    deadline = time.time() + 10
    while time.time() < deadline and _shortcut_row(page) is None:
        time.sleep(0.25)

    page.keyboard.press(
        "Enter"
    )

    if _wait_shortcut_inserted(page, 6):
        print(
            "  Shortcut inserted successfully."
        )
        return

    # --------------------------------------------------------
    # Fallback: click the exact '!<shortcut>' row. (No ArrowDown:
    # that could pick the NEXT shortcut, i.e. the wrong email.)
    # --------------------------------------------------------

    for _ in range(2):
        row = _shortcut_row(page)
        if row is None:
            break
        print("  Shortcut not expanded yet; clicking its row in the list...", flush=True)
        try:
            row.click(timeout=3000)
        except Exception:
            row_box = box(row)
            if row_box:
                page.mouse.click(
                    row_box["x"] + row_box["width"] / 2,
                    row_box["y"] + row_box["height"] / 2,
                )
        if _wait_shortcut_inserted(page, 6):
            print(
                "  Shortcut inserted successfully."
            )
            return

    _debug_dump(page, "shortcut_not_inserted")
    raise RuntimeError(
        f"Shortcut '{SHORTCUT}' could not be inserted: the message box still "
        "shows the shortcut name instead of the email text. Nothing was sent."
    )


# ============================================================
# CRISP - SEND
# ============================================================

def find_send_button(page):

    semantic_selectors = [

        'button[aria-label*="send" i]',

        'button[title*="send" i]',

        'button[data-testid*="send" i]',

        '[role="button"][aria-label*="send" i]',

        '[role="button"][title*="send" i]',
    ]

    for selector in semantic_selectors:

        try:

            locator = page.locator(
                selector
            )

            for i in range(
                locator.count()
            ):

                item = locator.nth(i)

                if (
                    visible(item)
                    and enabled(item)
                ):
                    return item

        except Exception:
            continue

    # --------------------------------------------------------
    # Find composer
    # --------------------------------------------------------

    composer = find_composer(
        page
    )

    if not composer:
        return None

    composer_box = box(
        composer
    )

    if not composer_box:
        return None

    cx = composer_box["x"]
    cy = composer_box["y"]
    cw = composer_box["width"]
    ch = composer_box["height"]

    right = cx + cw
    bottom = cy + ch

    candidates = []

    try:

        buttons = page.locator(
            "button"
        )

        for i in range(
            buttons.count()
        ):

            button = buttons.nth(i)

            if not visible(button):
                continue

            if not enabled(button):
                continue

            button_box = box(
                button
            )

            if not button_box:
                continue

            bx = button_box["x"]
            by = button_box["y"]
            bw = button_box["width"]
            bh = button_box["height"]

            if (
                bw < 20
                or bh < 20
            ):
                continue

            horizontal_ok = (
                bx >= right - 180
                and bx <= right + 20
            )

            vertical_ok = (
                by >= bottom - 100
                and by <= bottom + 20
            )

            if (
                horizontal_ok
                and vertical_ok
            ):

                candidates.append(
                    (
                        bx,
                        by,
                        bw,
                        bh,
                        button
                    )
                )

    except Exception:
        pass

    if candidates:

        candidates.sort(
            key=lambda item: (
                item[0] + item[2],
                item[1] + item[3]
            ),
            reverse=True
        )

        return candidates[0][4]

    return None


def send_message(page):

    if DRY_RUN:

        print(
            "  [DRY RUN] Send skipped."
        )

        return True

    print(
        "  Finding Send button..."
    )

    button = find_send_button(
        page
    )

    if not button:

        raise RuntimeError(
            "Could not find Crisp's blue Send button."
        )

    button_box = box(
        button
    )

    if button_box:

        print(
            "  Send button found at "
            f"x={button_box['x']:.0f}, "
            f"y={button_box['y']:.0f}, "
            f"w={button_box['width']:.0f}, "
            f"h={button_box['height']:.0f}"
        )

    else:

        print(
            "  Send button found."
        )

    if not enabled(button):

        raise RuntimeError(
            "Crisp Send button is disabled."
        )

    before = get_composer_text(
        find_composer(page)
    )

    if len(before.strip()) < 5:

        raise RuntimeError(
            "Composer appears empty. "
            "Refusing to send."
        )

    if SHORTCUT and SHORTCUT in before:

        raise RuntimeError(
            f"Composer still shows the shortcut name '{SHORTCUT}' instead of "
            "the email text. Refusing to send (nothing was sent)."
        )

    before_send_click()   # shared Sheet: record "Sending" and re-check the claim

    print(
        "  Clicking Send..."
    )

    global SEND_CLICKED
    SEND_CLICKED = True

    button.click(
        force=True
    )

    # --------------------------------------------------------
    # Verify send
    # --------------------------------------------------------
    # The sent text must no longer be present in ANY visible composer box.
    # (The old check only looked at the lowest box, which can be a different
    # element after Crisp re-renders the conversation.)

    key = re.sub(r"\s+", " ", before).strip()[:30]

    deadline = time.time() + SEND_WAIT + 6

    while time.time() < deadline:

        texts = all_composer_texts(page)

        still_has_text = any(
            key and key in re.sub(r"\s+", " ", t)
            for t in texts
        )

        if not still_has_text:
            print("  Email sent successfully.", flush=True)
            emit("sent")
            return True

        time.sleep(0.4)

    # Not confirmed: collect evidence, but do NOT retry automatically
    # (a retry could send a duplicate email).
    try:
        shot = DEBUG_DIR / "debug_send_unconfirmed.png"
        page.screenshot(path=str(shot))
        print(f"  Debug screenshot saved: {shot}", flush=True)
    except Exception:
        pass
    try:
        for i, t in enumerate(all_composer_texts(page)):
            print(f"  Composer box {i} text now: {t[:120]!r}", flush=True)
        btn_now = find_send_button(page)
        print(
            "  Send button still found: "
            f"{bool(btn_now)} | enabled: {enabled(btn_now) if btn_now else None}",
            flush=True,
        )
    except Exception:
        pass

    raise RuntimeError(
        "Send button was clicked, but Crisp did not confirm the send. "
        "The email MAY already have been sent - check the conversation in "
        "Crisp before running again, to avoid duplicates."
    )


def all_composer_texts(page):
    """Text of every visible composer-like box in the lower part of the page."""
    texts = []
    for selector in (
        'textarea[placeholder*="message" i]',
        '[contenteditable="true"]',
        '[role="textbox"]',
    ):
        try:
            loc = page.locator(selector)
            for i in range(loc.count()):
                item = loc.nth(i)
                if not visible(item):
                    continue
                bb = box(item)
                if not bb or bb["y"] < 350:
                    continue
                texts.append(get_composer_text(item))
        except Exception:
            continue
    return texts


# ============================================================
# CRISP - ADD SEGMENT
# ============================================================

def add_segment(page):
    if API_MODE:
        return            # the segment was set with the conversation (Crisp API)

    print(
        f"  Adding segment: {SEGMENT_NAME}"
    )

    # Find the actual Crisp segment input.
    segment_field = page.locator(
        'input[placeholder="Segment conversation…"]'
    ).last

    try:
        segment_field.wait_for(
            state="visible",
            timeout=10000
        )

    except Exception:

        segment_field = page.locator(
            'input[placeholder*="Segment conversation"]'
        ).last

        segment_field.wait_for(
            state="visible",
            timeout=10000
        )

    # Focus the field.
    segment_field.scroll_into_view_if_needed()

    segment_field.click(
        force=True
    )

    segment_field.focus()

    time.sleep(0.3)

    # Clear anything already inside.
    try:

        segment_field.fill("")

    except Exception:

        page.keyboard.press(
            "Control+A"
        )

        page.keyboard.press(
            "Backspace"
        )

    # Type exactly like the working debug version.
    print(
        f"  Typing segment: {SEGMENT_NAME}"
    )

    for character in SEGMENT_NAME:

        page.keyboard.insert_text(
            character
        )

        time.sleep(0.08)

    # Crisp automatically adds the segment after Enter.
    time.sleep(0.5)

    page.keyboard.press(
        "Enter"
    )

    time.sleep(1.5)

    # Verify that the segment was actually attached.
    labels = page.locator(
        ".c-profile-card-segments__segment "
        ".c-base-tag__label"
    )

    for i in range(labels.count()):

        item = labels.nth(i)

        try:

            if not visible(item):
                continue

            text = (
                item.inner_text()
                or ""
            ).strip()

            if (
                text.lower()
                == SEGMENT_NAME.lower()
            ):

                print(
                    f"  Segment '{SEGMENT_NAME}' "
                    "added successfully."
                )

                return True

        except Exception:
            continue

    raise RuntimeError(
        f"Segment '{SEGMENT_NAME}' was not added."
    )
# ============================================================
# CRISP - RESOLVE
# ============================================================

def resolve_conversation(page):
    if API_MODE:
        if API_SESSION:
            API.set_state(API_SESSION, "resolved")
            print("  Conversation resolved (Crisp API).")
        return

    if DRY_RUN:

        print(
            "  [DRY RUN] Resolve skipped."
        )

        return True

    print(
        "  Resolving conversation..."
    )

    try:

        # Crisp shortcut:
        # Ctrl + Alt + R = Resolve conversation

        print(
            "  Pressing Ctrl+Alt+R..."
        )

        page.keyboard.press(
            "Control+Alt+r"
        )

        time.sleep(1.5)

        print(
            "  Conversation resolved."
        )

        return True

    except Exception as e:

        print(
            f"  Warning: Could not resolve conversation: {e}"
        )

        return False

# ============================================================
# ONE RECIPIENT
# ============================================================

def process_recipient(
    page,
    row
):
    if API_MODE:
        return api_send(row)

    email = clean(
        row.get("Contact Email")
    )

    # Never a blank or broken name: fall back to the part before the @.
    name, from_email = rules.display_name(row)

    print(
        f"  Recipient: {email} | {name}" + (" (name from email)" if from_email else "")
    )

    # --------------------------------------------------------
    # 1. New conversation
    # --------------------------------------------------------

    open_new_conversation(
        page
    )

    # --------------------------------------------------------
    # 2. Fill details
    # --------------------------------------------------------

    fill_new_conversation(
        page,
        email,
        name
    )

    # --------------------------------------------------------
    # 3. Existing shortcut
    # --------------------------------------------------------

    insert_shortcut(
        page
    )

    # --------------------------------------------------------
    # 4. Send
    # --------------------------------------------------------

    sent = send_message(
        page
    )

    if not sent:
        return False

    # --------------------------------------------------------
    # 5. CSV is updated AFTER successful send.
    #
    # Segment/Resolve happen after this.
    # This prevents duplicate emails if either post-send
    # action has a temporary UI problem.
    # --------------------------------------------------------

    return True


def recover_page(page):
    if API_MODE:
        return True       # no page to recover
    """After a failure BEFORE Send: close dialogs and return to the Inbox."""
    try:
        for _ in range(3):
            page.keyboard.press("Escape")
            time.sleep(0.3)
        page.goto(CRISP_URL, wait_until="domcontentloaded", timeout=60000)
        wait_for_crisp(page)
        return True
    except Exception as exc:
        print(f"  WARNING: recovery failed: {exc}")
        return False


# ============================================================
# MAIN
# ============================================================

def main():
    """Run once; the CSV lock is always released when the run ends."""
    try:
        _main()
    finally:
        pace_done(0)
        end_claims("run ended", "Finished")
        release_lock()
        if SHEET is not None:
            SHEET.release_lock()


def current_conversation_link(page):
    """The open conversation's address, read live from the page ('' if none)."""
    try:
        url = str(page.evaluate("location.href") or "")
    except Exception:
        url = str(getattr(page, "url", "") or "")
    return url if "session_" in url else ""


def read_conversation(page, link, timeout_s=20):
    """Open one saved conversation link and read it. READ-ONLY: only page.goto and reads."""
    import campaign_results as cr
    page.goto(link, wait_until="domcontentloaded", timeout=60000)
    end = time.time() + timeout_s
    raw = {}
    while time.time() < end:
        raw = page.evaluate(cr.READ_JS)
        if raw.get("login_lost") or any(m.get("when") for m in raw.get("messages") or []):
            break
        page.wait_for_timeout(700)
    return raw


def check_links(page, items, out_path, delay=RESULTS_DELAY, stop=stop_requested):
    """Visit each link slowly and append one JSON line per conversation.
    Stops cleanly when the login is lost or the page layout is not recognised."""
    import campaign_results as cr
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    total, checked = len(items), 0
    for position, item in enumerate(items, start=1):
        if stop():
            print("Stop requested. Status check stopped.")
            emit("stop_requested")
            break
        emit("check_progress", position=position, total=total, email=item.get("email", ""))
        try:
            raw = read_conversation(page, item["link"])
        except Exception as exc:
            print(f"CHECK [{position}/{total}] {item.get('email', '')}: could not open ({type(exc).__name__})")
            raw = {"messages": [], "error": type(exc).__name__}
        result = cr.analyze(raw, item.get("sent_at", ""))
        if result.get("problem") == "login_lost":
            print("CHECK STOPPED: the Crisp login was lost. Sign in again, then refresh.")
            emit("check_stopped", reason="login_lost")
            break
        if result.get("problem") == "layout" and not raw.get("error"):
            print("CHECK STOPPED: the Crisp page layout was not recognised (no message times found).")
            emit("check_stopped", reason="layout")
            break
        with out.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": item["key"], "result": result,
                                     "observed_at": cr.fmt(datetime.now())}) + "\n")
        checked += 1
        print(f"CHECK [{position}/{total}] {item.get('email', '')}: "
              f"{result.get('status') or result.get('problem')}")
        if position < total:
            sc.sleep_seconds(delay, stop)
    emit("check_done", checked=checked, total=total)
    print(f"Status check: {checked} of {total} conversations read.")
    return checked


def results_check():
    """'Refresh statuses' from the GUI: open each conversation link (read-only)."""
    items = json.loads(Path(RESULTS_INPUT).read_text(encoding="utf-8")) if RESULTS_INPUT else []
    print(f"Campaign Results: checking {len(items)} conversation(s), read-only, "
          f"{RESULTS_DELAY:.0f} s between pages.")
    emit("check_start", total=len(items))
    if not items:
        return
    print("Launching Chromium. Complete your Crisp login when the browser opens.")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False,
            viewport={"width": 1600, "height": 900}, args=["--start-maximized"])
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(CRISP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        wait_for_crisp(page)
        check_links(page, items, RESULTS_OUTPUT)
        try:
            context.close()
        except Exception:
            pass


def health_check():
    """'Check Crisp': open Crisp and look for the subject dropdown, the
    Shortcuts tab and the segment field, and read the saved subjects and
    shortcut names (so the app can say which versions exist in Crisp).
    Never types into a message, never creates a conversation, never sends."""
    print("Crisp health check: nothing is created or sent.")
    emit("health_start")
    results = []

    def report(name, ok, detail):
        results.append(ok)
        print(f"HEALTH {'OK' if ok else 'FAIL'}: {name}: {detail}")
        emit("health", check=name, ok=bool(ok), detail=detail)

    print("Launching Chromium. Complete your Crisp login when the browser opens.")
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR), headless=False,
            viewport={"width": 1600, "height": 900}, args=["--start-maximized"])
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(CRISP_URL, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            pass
        wait_for_crisp(page)

        # 1) New conversation dialog + subject dropdown (dialog is closed, not submitted)
        try:
            open_new_conversation(page)
            field = _subject_input(page)
            field.wait_for(state="visible", timeout=ELEMENT_TIMEOUT)
            subjects = []
            try:                     # open the menu (nothing typed) and read its saved subjects
                field.click()
                page.wait_for_timeout(900)
                subjects = list(dict.fromkeys(t for _, t in _menu_rows(page) if t))
            except Exception:
                pass
            emit("crisp_lists", subjects=subjects)
            report("Subject dropdown", True, f"found in the New conversation dialog, {len(subjects)} saved "
                   "subject(s) read")
        except Exception as exc:
            report("Subject dropdown", False, f"{type(exc).__name__}: {exc}")
        finally:
            for _ in range(2):
                try:
                    page.keyboard.press("Escape")
                    time.sleep(0.3)
                except Exception:
                    pass

        # 2+3) need an existing conversation open; the user picks one.
        print("Open any existing conversation in the Crisp window (90 seconds)...")
        emit("health_wait", seconds=90)
        shortcuts = segment = False
        deadline = time.time() + 90
        while time.time() < deadline and not (shortcuts and segment) and not stop_requested():
            try:
                shortcuts = shortcuts or any(
                    visible(loc.nth(i))
                    for loc in (page.get_by_text("Shortcuts", exact=True),)
                    for i in range(loc.count()))
                seg = page.locator('input[placeholder*="Segment conversation"]')
                segment = segment or any(visible(seg.nth(i)) for i in range(seg.count()))
            except Exception:
                pass
            time.sleep(1)
        names = []
        if shortcuts:
            names = read_shortcut_names(page)
            emit("crisp_lists", shortcuts=names)
        report("Shortcuts tab", shortcuts, f"visible in the conversation, {len(names)} shortcut name(s) read"
               if shortcuts else "not found (open a conversation and click the message box, then check again)")
        report("Segment field", segment, "visible in the conversation profile" if segment
               else "not found (open a conversation with the profile panel visible)")
        passed = sum(1 for ok in results if ok)
        print(f"Health check: {passed} of {len(results)} passed.")
        emit("health_done", passed=passed, total=len(results))
        try:
            context.close()
        except Exception:
            pass


def read_shortcut_names(page):
    """Click the Shortcuts tab and read the listed '!name' rows. Nothing is typed or inserted."""
    try:
        found = [(box(item)["y"], item) for item in
                 (page.get_by_text("Shortcuts", exact=True).nth(i)
                  for i in range(page.get_by_text("Shortcuts", exact=True).count()))
                 if visible(item) and box(item)]
        if not found:
            return []
        max(found, key=lambda x: x[0])[1].click(timeout=3000)   # the composer's tab (lowest on screen)
        page.wait_for_timeout(1200)
        rows = page.get_by_text(re.compile(r"^\s*![^\s!]+\s*$"))
        names = []
        for i in range(min(rows.count(), 500)):
            item = rows.nth(i)
            if visible(item):
                names.append(item.inner_text().strip().lstrip("!"))
        return list(dict.fromkeys(n for n in names if n))
    except Exception:
        return []


class _NoBrowser:
    """Stands in for sync_playwright() when sending through the Crisp API."""

    def __enter__(self):
        return None

    def __exit__(self, *exc):
        return False


def open_crisp_page(p):
    """Start Chromium with the saved Crisp login and wait for the Inbox (browser mode)."""

    context = (
        p.chromium.launch_persistent_context(
            user_data_dir=str(
                PROFILE_DIR
            ),
            headless=False,
            viewport={
                "width": 1600,
                "height": 900
            },
            args=[
                "--start-maximized"
            ]
        )
    )

    if context.pages:

        page = context.pages[0]

    else:

        page = context.new_page()

    try:

        page.goto(
            CRISP_URL,
            wait_until="domcontentloaded",
            timeout=60000
        )

    except Exception:
        pass

    wait_for_crisp(
        page
    )

    print()
    print("Browser is open. Waiting for automatic Inbox detection.")
    print("Complete Crisp login in Chromium if requested.")
    # wait_for_crisp() above returns only after the authenticated Inbox
    # is detected, so no manual Enter prompt is needed here.

    return context, page


def _main():
    if HEALTH_CHECK:
        health_check()
        return
    if RESULTS_CHECK:
        results_check()
        return

    print("=" * 60)
    print("Crisp Email Agent")
    print("=" * 60)

    print(
        f"CSV: {CSV_FILE}"
    )

    if API_MODE:
        print(f"Send with: Crisp API ({len(API_TEXTS)} message text(s)); no browser.")
    if len(VARIANTS) > 1:
        print("A/B test, split evenly: " + " · ".join(
            f"{n} ({lab})" + (f" / subject: {s}" if len(set(SUBJECTS)) > 1 else "")
            for (n, lab), s in zip(VARIANTS, SUBJECTS)))
    elif not API_MODE:
        print(
            f"Shortcut: {SHORTCUT}"
        )
    try:
        if API_MODE:
            sc.ab_plan(", ".join(f"api-{i + 1}" for i in range(max(1, len(API_TEXTS)))),
                       os.environ.get("CRISP_SHORTCUT_VERSION", ""), SUBJECT_TEXT)
        else:
            sc.ab_plan(os.environ.get("CRISP_SHORTCUT", SHORTCUT), os.environ.get("CRISP_SHORTCUT_VERSION", ""),
                       SUBJECT_TEXT)
    except ValueError as exc:
        raise RuntimeError(f"Message / shortcut / subject settings: {exc}")

    print(
        f"Subject: {SUBJECT_TEXT}"
    )

    print(
        f"Reference filter: {REFERENCE_FILTER}"
    )

    print(
        f"Segment: {SEGMENT_NAME}"
    )

    print(
        f"Dry run: {DRY_RUN}"
    )
    emit("run_start", dry_run=DRY_RUN, csv=CSV_FILE.name, subject=SUBJECT_TEXT, campaign=CAMPAIGN,
         delay=40.0 if RANDOM_DELAY else (float(DELAY_ENV) if DELAY_ENV else BETWEEN_EMAILS))

    if SOURCE == "sheet":
        open_sheet()
        load_shared_state()

    rows, fieldnames = load_csv()

    if ROW_FROM or ROW_TO:
        first, last = row_range(len(rows))
        print(f"Row range: {first}-{last} of {len(rows)}")

    window = sc.parse_window(SEND_WINDOW)
    if START_AT:
        sc.parse_hhmm(START_AT)

    acquire_lock()
    print_precheck(rows)
    if RETRY_ONLY:
        print("Retry failed only: rows whose Status is Failed or Skipped.")

    if RUN_DIR:
        RUN_DIR.mkdir(parents=True, exist_ok=True)
        (RUN_DIR / "recipients_before.csv").write_bytes(CSV_FILE.read_bytes())

    if not DRY_RUN and SHEET is not None:
        SHEET.mark_shared_run()      # keeps older app versions out; never blocks 3.8+ runners
        SHEET.start_heartbeat()
        CLAIMS.start()
        wanted = ["Mail Sent"] + (["Reference"] if REFERENCE_FILTER else []) + EXTRA_COLUMNS
        if [c for c in wanted if c not in SHEET.colmap and c not in SHEET.header]:
            # Adding columns is the one change that needs everyone else to wait a moment.
            if not CLAIMS.schema_lock():
                raise RuntimeError("Another runner is changing the Sheet's columns. Try again in a minute.")
            try:
                SHEET.download()                     # fresh header under the lock
                SHEET.ensure_columns(wanted)
            finally:
                CLAIMS.schema_unlock()
        print(f"Shared sending on tab {SHEET_TAB!r}: recipients are claimed {CLAIM_BATCH} at a time.")

    if not DRY_RUN:
        if get_eligible(rows):
            backup_csv()
        if ensure_columns(fieldnames, rows):
            save_csv(rows, fieldnames)
        mark_precheck(rows, fieldnames)

    print()

    eligible = apply_limits(get_eligible(
        rows
    ))

    print(
        f"Eligible recipients: "
        f"{len(eligible)}"
    )
    emit("eligible", count=len(eligible))

    print()

    if not eligible:

        print(
            "No eligible recipients."
        )
        emit("no_eligible")

        return

    if START_AT:
        target = sc.next_occurrence(START_AT)
        print(f"Scheduled start: waiting until {target:%Y-%m-%d %H:%M} Dhaka time. "
              "Keep this computer on and awake.")
        if not sc.sleep_until(target, stop_requested):
            print("Stop requested. Stopping before the next recipient.")
            emit("stop_requested")
            return
        print("Scheduled time reached.")

    if API_MODE:
        open_api()
    else:
        print("Launching Chromium. Complete your Crisp login when the browser opens.")

    with (_NoBrowser() if API_MODE else sync_playwright()) as p:

        context, page = (None, None) if API_MODE else open_crisp_page(p)

        completed = 0
        failed = 0
        skipped = 0
        fails_in_row = 0
        retry = []   # (email, name, reason) not sent this run
        last_email = ""

        for position, (
            row_index,
            row
        ) in enumerate(
            eligible,
            start=1
        ):

            email = clean(
                row.get("Contact Email")
            )

            name = rules.display_name(row)[0]

            if PAUSE_FILE and Path(PAUSE_FILE).exists() and not stop_requested():
                after = last_email or "the start"
                print(f"Paused after {after}. Waiting for Resume...")
                emit("paused", after=last_email)
                if CLAIMS is not None:
                    CLAIMS.state = "Paused"      # claims are kept; the heartbeat keeps running
                while Path(PAUSE_FILE).exists() and not stop_requested():
                    time.sleep(0.5)
                if CLAIMS is not None:
                    CLAIMS.state = "Running"
                if not stop_requested():
                    print("Resumed.")
                    emit("resumed")

            if stop_requested():
                print()
                print("Stop requested. Stopping before the next recipient.")
                emit("stop_requested")
                break

            if window and not sc.in_window(sc.now_dhaka(), window):
                opens = sc.window_opens_later_today(sc.now_dhaka(), window)
                if opens is None:
                    print()
                    print(f"Send window {SEND_WINDOW} (Dhaka) has ended. Stopping; "
                          "the remaining recipients were not sent.")
                    emit("window_ended", window=SEND_WINDOW)
                    break
                print(f"Outside send window {SEND_WINDOW} (Dhaka). Waiting until {opens:%H:%M}...")
                if not sc.sleep_until(opens, stop_requested):
                    print("Stop requested. Stopping before the next recipient.")
                    emit("stop_requested")
                    break

            if SEND_LIMIT is not None and completed >= SEND_LIMIT:
                print(f"Limit reached: {completed} sent.")
                break

            if CLAIMS is not None:
                try:
                    why = shared_gate(eligible, position - 1, email)
                except Exception as exc:  # noqa: BLE001
                    print(f"Could not check the shared Sheet ({exc}). Stopping; nothing more is sent.")
                    emit("error", message=f"Shared Sheet not reachable: {exc}")
                    break
                if why:
                    print(f"  Not sent by this runner: {email} ({why}).")
                    emit("claimed_elsewhere", email=email, reason=why, position=position, total=len(eligible))
                    continue

            if not pace_turn():
                if stop_requested():
                    print("Stop requested. Stopping before the next recipient.")
                    emit("stop_requested")
                break

            print()
            print("=" * 60)

            print(
                f"[{position}/{len(eligible)}] "
                f"{email} | {name}"
            )
            emit("recipient", position=position, total=len(eligible), email=email, name=name,
                 row=row_index + 1)

            print("=" * 60)

            global SEND_CLICKED, CURRENT_EMAIL
            SEND_CLICKED = False
            CURRENT_EMAIL = email
            last_email = email
            variant = choose_variant()
            if len(VARIANTS) > 1:
                print(f"  Shortcut: {SHORTCUT} ({SHORTCUT_VERSION})")
                if len(set(SUBJECTS)) > 1:
                    print(f"  Subject: {SUBJECT}")
                emit("variant", email=email, shortcut=SHORTCUT, version=SHORTCUT_VERSION)

            try:

                # ------------------------------------------------
                # SEND
                # ------------------------------------------------

                sent = process_recipient(
                    page,
                    row
                )

                if not sent:

                    failed += 1
                    result = sc.UNCONFIRMED if SEND_CLICKED else sc.FAILED
                    if SEND_CLICKED:
                        VARIANT_COUNTS[variant] += 1
                    record(rows, fieldnames, row_index, result, "Email was not sent.")
                    log_result(row_index, row, result, "Email was not sent.")
                    claim_result(email, result, "Email was not sent.")
                    retry.append((email, name, "Email was not sent."))
                    emit("failed", email=email, error="Email was not sent.", unconfirmed=SEND_CLICKED)

                    print(
                        "  Email was not sent."
                    )

                    print(
                        "  Agent stopped safely."
                    )

                    break

                # ------------------------------------------------
                # MAIL SENT CSV UPDATE
                # ------------------------------------------------

                link = API_LINK if API_MODE else current_conversation_link(page)

                if not DRY_RUN:

                    sent_row = rows[row_index]
                    rules.set_cell(sent_row, COLMAP, "Mail Sent", "TRUE")
                    rules.set_cell(sent_row, COLMAP, "Sent At", sc.stamp())
                    rules.set_cell(sent_row, COLMAP, "Status", sc.SENT)
                    rules.set_cell(sent_row, COLMAP, "Error", "")
                    rules.set_cell(sent_row, COLMAP, "Conversation link", link)
                    if REFERENCE_FILTER and not clean(sent_row.get("Reference")):
                        # Empty Reference: the row now belongs to whoever sent it.
                        rules.set_cell(sent_row, COLMAP, "Reference", REFERENCE_FILTER)

                    save_csv(
                        rows,
                        fieldnames
                    )
                    sync_row(rows, row_index)

                    print(
                        "  Mail Sent marked TRUE."
                    )

                # ------------------------------------------------
                # SEGMENT (skip all post-send actions in dry-run)
                # ------------------------------------------------

                if DRY_RUN:
                    print("  [DRY RUN] Segment and resolve skipped.")
                else:
                    try:

                        add_segment(
                            page
                        )

                    except Exception as e:

                        print(
                            f"  WARNING: Segment failed: {e}"
                        )

                        print(
                            "  Email was already sent "
                            "and CSV was updated."
                        )

                    # ------------------------------------------------
                    # RESOLVE
                    # ------------------------------------------------

                    try:

                        resolve_conversation(
                            page
                        )

                    except Exception as e:

                        print(
                            f"  WARNING: Resolve failed: {e}"
                        )

                        print(
                            "  Email was already sent "
                            "and CSV was updated."
                        )

                completed += 1
                VARIANT_COUNTS[variant] += 1
                fails_in_row = 0
                log_result(row_index, row, sc.DRY_RUN if DRY_RUN else sc.SENT, link=link)
                claim_result(email, sc.SENT, link)
                emit("success", email=email, result=sc.DRY_RUN if DRY_RUN else sc.SENT, link=link)

                print(
                    "  SUCCESS."
                )

            except KeyboardInterrupt:

                print()
                print(
                    "Stopped by user."
                )

                break

            except claims.ClaimLost as exc:
                # Lost the claim before the Send click: nothing was sent, and the row
                # belongs to another runner now, so no Status is written here.
                print(f"  Not sent by this runner: {email} ({exc}). Nothing was sent.")
                emit("claimed_elsewhere", email=email, reason=str(exc), position=position, total=len(eligible))
                if CLAIMS is not None and rules.normalize_email(email) in CLAIMS.mine:
                    CLAIMS.mine.remove(rules.normalize_email(email))
                pace_done(0)
                if not recover_page(page):
                    print("  Could not get Crisp back to the Inbox. Stopping.")
                    break
                continue

            except Exception as e:

                reason = f"{type(e).__name__}: {e}"
                fails_in_row += 1

                if (
                    SKIP_FAILED
                    and not SEND_CLICKED
                    and skipped < MAX_SKIPS
                    and fails_in_row < MAX_FAILS_IN_ROW
                ):
                    skipped += 1
                    retry.append((email, name, reason))
                    print(f"  SKIPPED: {reason}")
                    emit("skipped", email=email, error=reason)
                    failure_screenshot(page, position, email)
                    record(rows, fieldnames, row_index, sc.SKIPPED, reason)
                    log_result(row_index, row, sc.SKIPPED, reason)
                    claim_result(email, sc.SKIPPED, reason)
                    print("  Failed before Send, so nothing was sent. Continuing with the next recipient.")
                    pace_done(0)
                    if not recover_page(page):
                        print("  Could not get Crisp back to the Inbox. Stopping.")
                        break
                    continue

                failed += 1
                retry.append((email, name, reason))
                result = sc.UNCONFIRMED if (SEND_CLICKED or "MAY already have been sent" in reason) else sc.FAILED
                failure_screenshot(page, position, email)
                record(rows, fieldnames, row_index, result, reason)
                # Unconfirmed: keep the conversation's address so it can be checked with one click.
                link = current_conversation_link(page) if result == sc.UNCONFIRMED else ""
                log_result(row_index, row, result, reason, link=link)
                claim_result(email, result, reason)
                emit("failed", email=email, error=reason, unconfirmed=result == sc.UNCONFIRMED)

                if SKIP_FAILED and fails_in_row >= MAX_FAILS_IN_ROW:
                    print(f"  {fails_in_row} failures in a row: auto-stop.")

                print(
                    f"  FAILED: "
                    f"{reason}"
                )

                print(
                    "  No Mail Sent mark was added."
                )

                print(
                    "  Agent stopped rather than "
                    "risk sending to the wrong person."
                )

                break

            handed = pace_done()
            if handed is not None:
                # The wait now happens when the next send turn is taken (any campaign).
                emit("waiting", seconds=round(handed, 1))
                if handed >= 10 and position < len(eligible):
                    print(f"  Next email in {handed:.0f} s (app-wide pace).")
            elif position < len(
                eligible
            ):

                wait = between_delay()
                emit("waiting", seconds=round(wait, 1))
                if wait >= 10:
                    print(f"  Waiting {wait:.0f} s before the next email...")
                sc.sleep_seconds(wait, stop_requested)

        print()
        print("=" * 60)

        print(
            f"Completed: {completed}"
        )

        print(
            f"Failed:    {failed}"
        )

        if skipped:
            print(f"Skipped:   {skipped}")
        emit("summary", completed=completed, failed=failed, skipped=skipped)

        if retry:
            print("Retry list (not sent in this run):")
            for r_email, r_name, r_reason in retry:
                print(f"RETRY: {r_email} | {r_name} | {r_reason}")
                emit("retry", email=r_email, name=r_name, error=r_reason)

        print(
            f"CSV:       {CSV_FILE}"
        )

        write_run_files()

        end_claims("stopped" if stop_requested() else "run ended", "Stopped" if stop_requested() else "Finished")

        if SHEET is not None and not DRY_RUN:
            left = SHEET_QUEUE.flush(SHEET)
            print("Sheet:     all results written." if not left else
                  f"Sheet:     {len(left)} result(s) not written yet; retried at the next start.")

        print("=" * 60)

        print()
        print(
            "Browser will remain open for review."
        )

        if os.environ.get("CRISP_GUI_MODE") != "1" and context is not None:
            try:
                input("Press ENTER to close the browser...")
            except KeyboardInterrupt:
                pass

        try:

            if context is not None:
                context.close()

        except Exception:
            pass


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\nStopped by user."
        )

    except Exception as exc:

        print(
            f"\nERROR: "
            f"{type(exc).__name__}: {exc}"
        )
        emit("error", message=f"{type(exc).__name__}: {exc}")

        # In the GUI nobody can press Enter, so waiting here would hang the
        # process forever. Exit with a failure code instead.
        if os.environ.get("CRISP_GUI_MODE") == "1":
            raise SystemExit(1)

        try:

            input(
                "Press ENTER to exit..."
            )

        except (KeyboardInterrupt, EOFError):
            pass

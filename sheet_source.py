"""Google Sheet as the recipient source (CSV stays the default and fallback).

How a Sheet run works (shared by the agent and the GUI):
- The Sheet is downloaded into a local working CSV and the agent runs on it
  exactly as on a normal CSV.
- Every result is saved locally first, then written to the Sheet row found
  by EMAIL (never by row number). If that write fails, it goes to a pending
  queue on disk; the next run retries it first, and those emails count as
  already sent, so a dropped connection can never cause a re-send.
- Writes are paced (about 1 per second) with back-off on rate limits.
- Several runners may use one Sheet at the same time: each recipient is
  claimed separately (see claims.py). There is no whole-Sheet lock any more.
- The hidden "_crisp_control" tab row 1 says "Running: shared mode" while
  any 3.8+ runner is active. Older app versions (which only know the old
  whole-Sheet lock) read that and refuse to start, so they cannot send
  alongside claim-aware runners. An older version's own lock blocks 3.8+.

gspread/google-auth are imported only when a Sheet is used.
"""

from __future__ import annotations

import csv
import json
import re
import socket
import threading
import time
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import recipient_rules as rules  # noqa: E402  (same column rules as CSV files)

REQUIRED_COLUMNS = ("Contact Email",)   # only the email column is required
CONTROL_TAB = "_crisp_control"
STALE_MINUTES = 5
SHARED_OWNER = "__claims__"     # control-tab owner written by claim-aware (3.8+) runners
SHARED_TEXT = "Running: shared mode (app 3.8+). Older app versions must update before sending."
HEARTBEAT_SECONDS = 30
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class SheetError(RuntimeError):
    """A Sheet problem with a message the user can act on."""


def sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})", str(url or ""))
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", str(url or "").strip()):
        return str(url).strip()
    raise SheetError("That is not a Google Sheet link (it should contain /spreadsheets/d/...).")


def key_problem(key_path: str, app_root: Path) -> str:
    """'' if the service-account key file is usable and stored safely."""
    text = str(key_path or "").strip()
    if not text:
        return "Choose the service-account key file (.json)."
    path = Path(text).expanduser()
    if not path.is_file():
        return "Key file not found."
    try:
        path.resolve().relative_to(Path(app_root).resolve())
        return "Move the key file OUTSIDE the app folder (it must never be uploaded or zipped)."
    except ValueError:
        pass
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "The key file is not valid JSON."
    if data.get("type") != "service_account" or "client_email" not in data:
        return "This is not a service-account key (type must be service_account)."
    return ""


def service_account_email(key_path: str) -> str:
    try:
        return json.loads(Path(key_path).expanduser().read_text(encoding="utf-8")).get("client_email", "")
    except (OSError, ValueError):
        return ""


RETRY_CODES = {429, 500, 502, 503, 504}
HTTP_TIMEOUT = 30   # seconds per Google request; never wait forever


def _chain(exc):
    """The exception and everything it was raised from (gspread wraps 403 in PermissionError)."""
    seen = []
    while exc is not None and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def google_info(exc) -> dict:
    """{'code', 'status', 'message', 'reasons', 'activation_url'} from a Google API error."""
    info = {"code": 0, "status": "", "message": "", "reasons": [], "activation_url": ""}
    for e in _chain(exc):
        resp = getattr(e, "response", None)
        code = int(getattr(resp, "status_code", 0) or 0)
        if not code:
            continue
        info["code"] = code
        try:
            err = resp.json().get("error", {})
        except Exception:  # noqa: BLE001
            err = {}
        info["status"] = str(err.get("status") or "")
        info["message"] = str(err.get("message") or "")
        for d in err.get("details") or []:
            if d.get("reason"):
                info["reasons"].append(d["reason"])
            url = (d.get("metadata") or {}).get("activationUrl")
            if url:
                info["activation_url"] = url
        break
    return info


def _status_code(exc) -> int:
    return google_info(exc)["code"]


def is_retryable(exc) -> bool:
    """Only rate limits, Google server errors and network drops. Never 'access denied'."""
    code = _status_code(exc)
    if code:
        return code in RETRY_CODES
    if isinstance(exc, (PermissionError, FileNotFoundError)):
        return False
    try:
        import requests
        if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
            return True
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError))


def describe(exc, who: str = "") -> str:
    """A message that says what to do, not just what failed."""
    who = who or "the service account email (inside the key file)"
    info = google_info(exc)
    code, msg = info["code"], info["message"]
    if "SERVICE_DISABLED" in info["reasons"] or "has not been used in project" in msg or "is disabled" in msg:
        link = info["activation_url"] or "https://console.cloud.google.com/apis/library/sheets.googleapis.com"
        return ("The Google Sheets API is turned off in your Google Cloud project. "
                f"Open {link} , click Enable, wait 2 minutes, then try again.")
    if code == 403 or (not code and isinstance(exc, PermissionError)):
        return f"Google refused access (403). Open the Sheet > Share > add {who} as Editor, then try again."
    if code == 404 or type(exc).__name__ == "SpreadsheetNotFound":
        return f"Sheet not found (404). Check the link, and that the Sheet is shared with {who}."
    if code == 429:
        return "Google's rate limit was reached. Wait a minute, then try again."
    if code:
        return f"Google Sheets error {code}: {msg or exc}"
    if is_retryable(exc):
        return f"Could not reach Google (internet down, blocked or too slow): {type(exc).__name__}: {exc}"
    return f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__


class Pacer:
    """At most one Google request per `interval` seconds; retry only temporary errors, with back-off."""

    def __init__(self, interval: float = 1.1, tries: int = 4, clock=None, sleep=None):
        self.interval, self.tries = interval, tries
        self.clock = clock or (lambda: time.monotonic())
        self.sleep = sleep or (lambda seconds: time.sleep(seconds))
        self.who = ""   # service-account email, used in error messages
        self._last = -1e9
        self._lock = threading.Lock()

    def call(self, fn, *args, **kwargs):
        delay = 2.0
        for attempt in range(1, self.tries + 1):
            with self._lock:
                wait = self._last + self.interval - self.clock()
                if wait > 0:
                    self.sleep(wait)
                self._last = self.clock()
                try:
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    if not is_retryable(exc) or attempt == self.tries:
                        raise SheetError(describe(exc, self.who)) from exc
            self.sleep(delay)
            delay *= 2
        raise SheetError("Google Sheets did not respond.")


def _a1(row: int, col: int) -> str:
    letters = ""
    while col:
        col, rem = divmod(col - 1, 26)
        letters = chr(65 + rem) + letters
    return f"{letters}{row}"


class SheetSource:
    def __init__(self, url: str, tab: str, key_path: str, client=None, pacer: Pacer | None = None):
        self.url, self.tab, self.key_path = url, tab, key_path
        self.id = sheet_id(url)
        self._client = client
        self.pacer = pacer or Pacer()
        self.pacer.who = service_account_email(key_path)
        self.sh = None
        self.ws = None
        self.header: list[str] = []
        self.colmap: dict = {}   # canonical column -> the Sheet's own header
        self.data_rows = 0
        self.owner = f"{socket.gethostname()}"
        self._beat_stop = threading.Event()

    # ------------------------------------------------------------ connect
    def connect(self):
        if self._client is None:
            try:
                import gspread
            except ImportError as exc:
                raise SheetError("Google Sheet support is not installed. Run the update or setup again.") from exc
            try:
                self._client = gspread.service_account(filename=str(Path(self.key_path).expanduser()), scopes=SCOPES)
            except Exception as exc:  # noqa: BLE001
                raise SheetError(f"Could not use the key file: {exc}") from exc
            if hasattr(self._client, "set_timeout"):
                self._client.set_timeout(HTTP_TIMEOUT)
        self.sh = self.pacer.call(self._client.open_by_key, self.id)
        return self

    def tabs(self) -> list[str]:
        return [ws.title for ws in self.pacer.call(self.sh.worksheets) if ws.title != CONTROL_TAB]

    def worksheet(self):
        if self.ws is None:
            if not self.tab:
                raise SheetError("Choose the Sheet tab.")
            try:
                self.ws = self.pacer.call(self.sh.worksheet, self.tab)
            except SheetError as exc:
                raise SheetError(f"Tab '{self.tab}' not found in the Sheet.") from exc
        return self.ws

    # ------------------------------------------------------------ rows
    def download(self) -> tuple[list[dict], list[str]]:
        values = self.pacer.call(self.worksheet().get_all_values)
        if not values or not any(values[0]):
            raise SheetError("The Sheet tab is empty (no header row).")
        self.header = [h.strip() for h in values[0]]
        self.colmap = rules.map_columns(self.header)
        if "Contact Email" not in self.colmap:
            raise SheetError(rules.NO_EMAIL_COLUMN + " (in the Sheet tab)")
        self.data_rows = len(values) - 1
        rows = []
        for raw in values[1:]:
            raw = list(raw) + [""] * (len(self.header) - len(raw))
            rows.append({h: raw[i] for i, h in enumerate(self.header) if h})
        return rows, [h for h in self.header if h]

    @staticmethod
    def write_local(path: Path, rows: list[dict], fieldnames: list[str]) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def ensure_columns(self, columns) -> list[str]:
        """Add missing columns (canonical names) at the end of row 1.
        A new Mail Sent column is filled with FALSE (unticked) on every row."""
        missing = [c for c in columns if c not in self.colmap and c not in self.header]
        if not missing:
            return []
        start = len(self.header) + 1
        self.pacer.call(self.worksheet().update, values=[missing], range_name=_a1(1, start),
                        value_input_option="RAW")
        self.header.extend(missing)
        for c in missing:
            self.colmap[c] = c
        if "Mail Sent" in missing and self.data_rows:
            col = self.header.index("Mail Sent") + 1
            self.pacer.call(self.worksheet().update, values=[["FALSE"]] * self.data_rows,
                            range_name=_a1(2, col), value_input_option="USER_ENTERED")
        return missing

    def _col(self, canon: str) -> str:
        return self.colmap.get(canon, canon)

    def find_row(self, email: str, prefer_row: int | None = None) -> int | None:
        """Sheet row number (1 = header) holding this email, re-read fresh."""
        col = self.header.index(self._col("Contact Email")) + 1
        cells = self.pacer.call(self.worksheet().col_values, col)
        target = str(email or "").strip().lower()
        matches = [i + 1 for i, v in enumerate(cells) if i > 0 and str(v).strip().lower() == target]
        if not matches:
            return None
        if prefer_row in matches:
            return prefer_row
        return min(matches, key=lambda r: abs(r - (prefer_row or r)))

    def push(self, email: str, values: dict, prefer_row: int | None = None) -> int:
        """Write values into the row with this email. 'TRUE' becomes a ticked checkbox."""
        row = self.find_row(email, prefer_row)
        if row is None:
            raise SheetError(f"{email} is no longer in the Sheet; nothing was written there.")
        data = [{"range": _a1(row, self.header.index(self._col(col)) + 1), "values": [[str(val)]]}
                for col, val in values.items() if self._col(col) in self.header]
        if data:
            self.pacer.call(self.worksheet().batch_update, data, value_input_option="USER_ENTERED")
        return row

    # ------------------------------------------------------------ lock
    def _control(self):
        try:
            return self.pacer.call(self.sh.worksheet, CONTROL_TAB)
        except SheetError:
            ws = self.pacer.call(self.sh.add_worksheet, title=CONTROL_TAB, rows=5, cols=3)
            try:
                self.pacer.call(ws.hide)
            except SheetError:
                pass
            return ws

    def mark_shared_run(self, now=None) -> None:
        """Say "shared mode" in the control tab (keeps OLD app versions out).
        Other claim-aware runners are never blocked. Only an older version's
        fresh whole-Sheet lock blocks, because that version does not know claims."""
        now = now or datetime.now(timezone.utc)
        ws = self._control()
        values = self.pacer.call(ws.get_all_values) or [[]]
        first = (values[0] + ["", "", ""])[:3] if values else ["", "", ""]
        text, beat, owner = first
        if text.startswith("Running:") and owner and owner not in (self.owner, SHARED_OWNER):
            try:
                age = now - datetime.fromisoformat(beat)
            except ValueError:
                age = timedelta(0)
            if age < timedelta(minutes=STALE_MINUTES):
                raise SheetError(f"An older version of this app is sending from this Sheet ({text}). "
                                 "It does not know about shared sending. Update that computer to the "
                                 "latest version, or wait until its run ends.")
        self.pacer.call(ws.update, values=[[SHARED_TEXT, now.isoformat(), SHARED_OWNER]],
                        range_name="A1", value_input_option="RAW")
        self._control_ws = ws

    def is_marked_sent(self, email: str) -> bool:
        """Fresh read: is any row with this email ticked in Mail Sent right now?"""
        if self._col("Mail Sent") not in self.header:
            return False
        ws = self.worksheet()
        emails = self.pacer.call(ws.col_values, self.header.index(self._col("Contact Email")) + 1)
        marks = self.pacer.call(ws.col_values, self.header.index(self._col("Mail Sent")) + 1)
        target = str(email or "").strip().lower()
        for i, value in enumerate(emails):
            if i and str(value).strip().lower() == target and i < len(marks) and rules.is_checked(marks[i]):
                return True
        return False

    def heartbeat(self, now=None) -> None:
        now = now or datetime.now(timezone.utc)
        self.pacer.call(self._control_ws.update, values=[[now.isoformat()]], range_name="B1",
                        value_input_option="RAW")

    def start_heartbeat(self, interval: float = HEARTBEAT_SECONDS) -> None:
        def loop():
            while not self._beat_stop.wait(interval):
                try:
                    self.heartbeat()
                except Exception:  # noqa: BLE001
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def release_lock(self) -> None:
        """Stop the heartbeat. A shared-mode marker is left to go stale by itself
        (other runners may still be active); an old-style own lock is cleared."""
        self._beat_stop.set()
        ws = getattr(self, "_control_ws", None)
        if ws is None:
            return
        try:
            values = self.pacer.call(ws.get_all_values) or [[]]
            owner = (values[0] + ["", "", ""])[2] if values else ""
            if owner == self.owner:
                self.pacer.call(ws.update, values=[["", "", ""]], range_name="A1", value_input_option="RAW")
        except SheetError:
            pass


class PendingQueue:
    """Sheet writes that failed; retried first next time. Stored as JSON on disk."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def items(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _save(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, sheet: str, tab: str, email: str, row: int, values: dict) -> None:
        items = self.items()
        items.append({"sheet": sheet, "tab": tab, "email": email, "row": row, "values": values,
                      "time": datetime.now().isoformat(timespec="seconds")})
        self._save(items)

    def add_claim(self, sheet: str, tab: str, row: list) -> None:
        """A claims-log row (e.g. Sent) that could not be written; appended later."""
        items = self.items()
        items.append({"sheet": sheet, "tab": tab, "kind": "claim", "row": row,
                      "time": datetime.now().isoformat(timespec="seconds")})
        self._save(items)

    def for_sheet(self, sheet: str, tab: str) -> list[dict]:
        return [i for i in self.items() if i.get("sheet") == sheet and i.get("tab") == tab]

    def flush(self, source: SheetSource) -> list[dict]:
        """Try every pending write for this Sheet/tab. Returns those still pending."""
        keep, remaining = [], []
        for item in self.items():
            if item.get("sheet") != source.id or item.get("tab") != source.tab:
                keep.append(item)
                continue
            try:
                if item.get("kind") == "claim":
                    import claims as _claims
                    ws = source.pacer.call(source.sh.worksheet, _claims.CLAIMS_TAB)
                    source.pacer.call(ws.append_rows, [item["row"]], value_input_option="RAW",
                                      insert_data_option="INSERT_ROWS", table_range="A1")
                else:
                    source.push(item["email"], item["values"], item.get("row"))
            except SheetError:
                keep.append(item)
                remaining.append(item)
        self._save(keep)
        return remaining

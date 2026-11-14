"""Per-recipient claims on a shared Google Sheet, so several runners (people,
computers, campaigns) can work on the same Sheet at the same time without
emailing anyone twice.

Google Sheets has no atomic compare-and-set, so claims are an APPEND-ONLY log
in the hidden tab "_crisp_claims". One row per event:

    Time (Dhaka) | Email | State | Runner | Run | Campaign | Note

A claim is (normalised email, run id). Its state is replayed from the log, in
log order. The rules every runner applies to the same log, so they always
agree:

1. Owner: among the claims on an email that are still active (Claimed,
   Sending, Unconfirmed), the one whose FIRST row is earliest in the log wins.
   Appends to one spreadsheet are applied one at a time, so every runner sees
   the same order. A runner appends its claim, re-reads, and keeps only the
   emails it owns (it writes Released for the ones it lost).
2. Released and Failed are final. Rows written for a claim after that are void,
   so a runner that was released while it was away can never "come back".
3. Sending is written just BEFORE the Send click and then re-checked. A claim
   in Sending or Unconfirmed is never released by anyone else automatically.
   Only its own run ("not sent: ...", before the click) or a person
   ("manual: ...") can close it.
4. Expiry: a Claimed claim (no Send click yet) whose runner has had no
   heartbeat for EXPIRY minutes may be released by another runner, with the
   reason written in the Note.

Heartbeats live in "_crisp_runners": one row per run, updated in place by that
run only (about every 30 s), so the Team panel can show who is running.

What this does NOT promise: if someone edits or sorts the _crisp_claims tab by
hand, or Google returns a read that misses a write made a moment earlier, two
runners could disagree for that moment. The Mail Sent re-check before every
send and the Sending re-check narrow this further, but it is not zero.
"""

from __future__ import annotations

import socket
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import send_control as sc  # noqa: E402

CLAIMS_TAB = "_crisp_claims"
RUNNERS_TAB = "_crisp_runners"
CLAIM_HEADER = ["Time (Dhaka)", "Email", "State", "Runner", "Run", "Campaign", "Note"]
RUNNER_HEADER = ["Run", "Runner", "Campaign", "Started (Dhaka)", "Last seen (Dhaka)", "State", "Sent", "Claimed"]

CLAIMED, SENDING, SENT, FAILED, UNCONFIRMED, RELEASED = (
    "Claimed", "Sending", "Sent", "Failed", "Unconfirmed", "Released")
ACTIVE = {CLAIMED, SENDING, UNCONFIRMED}        # competing for the email right now
LIVE = ACTIVE | {SENT}
FINAL = {RELEASED, FAILED, SENT}
NEEDS_CHECK = {SENDING, UNCONFIRMED}             # a person must check Crisp

DEFAULT_BATCH = 10
DEFAULT_EXPIRY_MIN = 5
HEARTBEAT_SECONDS = 30
SCHEMA_LOCK_MINUTES = 2


def norm(email) -> str:
    return str(email or "").strip().lower()


def parse_stamp(text: str):
    try:
        return datetime.strptime(str(text or "").strip()[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=sc.DHAKA)
    except ValueError:
        return None


def runner_label(name: str = "") -> str:
    host = socket.gethostname()
    name = str(name or "").strip()
    return f"{name} ({host})" if name and name != host else host


def new_run_id() -> str:
    return uuid.uuid4().hex[:10]


# ------------------------------------------------------------------ replaying the log
@dataclass
class Claim:
    email: str
    run: str
    runner: str
    campaign: str
    claimed_at: str
    order: int
    state: str = CLAIMED
    updated_at: str = ""
    note: str = ""
    sending_order: int = -1      # log position of the Sending row (if any)

    def apply(self, state: str, note: str, at: str, order: int) -> None:
        if self.state in FINAL:
            return                                      # rule 2: final is final
        if self.state in NEEDS_CHECK:
            closing = note.startswith("not sent") or note.startswith("manual")
            if state == RELEASED and not closing:
                return                                  # rule 3: nobody else releases a Sending claim
            if state == FAILED and self.state == UNCONFIRMED and not closing:
                return
            if state == CLAIMED:
                return
        self.state, self.note, self.updated_at = state, note, at
        if state == SENDING and self.sending_order < 0:
            self.sending_order = order


def replay(values: list[list[str]]) -> dict[str, list[Claim]]:
    """{email: [claims in log order]} from the claims tab (row 1 = header)."""
    by_key: dict[tuple, Claim] = {}
    out: dict[str, list[Claim]] = {}
    for order, raw in enumerate(values[1:] if values else []):
        raw = list(raw) + [""] * 7
        at, email, state, runner, run, campaign, note = (str(x).strip() for x in raw[:7])
        email = norm(email)
        if not email or not run or state not in LIVE | FINAL:
            continue
        claim = by_key.get((email, run))
        if claim is None:
            claim = Claim(email, run, runner, campaign, at, order, state, at, note,
                          order if state == SENDING else -1)
            by_key[(email, run)] = claim
            out.setdefault(email, []).append(claim)
        else:
            claim.apply(state, note, at, order)
    return out


def owner(claims: list[Claim]) -> Claim | None:
    active = [c for c in claims if c.state in ACTIVE]
    return min(active, key=lambda c: c.order) if active else None


def sent_blocks(claim: Claim, campaign: str, recent_days: int, now: datetime) -> str:
    """A finished send in the shared log blocks a new send when it is in the same
    campaign, or within the recently-contacted window (0 days = off: another
    campaign may email them). In-progress claims are blocked elsewhere."""
    if claim.state != SENT:
        return ""
    if campaign and claim.campaign.strip().lower() == campaign.strip().lower():
        return f"already sent {claim.updated_at} in '{claim.campaign}' by {claim.runner}"
    days = int(recent_days or 0)
    if days <= 0:
        return ""
    at = parse_stamp(claim.updated_at)
    if at is None or now - at < timedelta(days=days):
        return f"sent {claim.updated_at} in '{claim.campaign}' by {claim.runner}"
    return ""


# ------------------------------------------------------------------ snapshot (read-only)
@dataclass
class Snapshot:
    """What the claims log and the runner heartbeats say right now."""
    claims: dict = field(default_factory=dict)
    runners: dict = field(default_factory=dict)        # run id -> row dict
    now: datetime | None = None
    expiry_min: int = DEFAULT_EXPIRY_MIN
    campaign: str = ""
    recent_days: int = 0

    def last_seen(self, run: str):
        row = self.runners.get(run)
        return parse_stamp(row.get("Last seen (Dhaka)", "")) if row else None

    def expired(self, claim: Claim) -> str:
        """Reason text if this Claimed claim may be released by someone else, else ''."""
        if claim.state != CLAIMED:
            return ""
        now = self.now or sc.now_dhaka()
        seen = self.last_seen(claim.run) or parse_stamp(claim.claimed_at)
        if seen is None:
            return f"expired: no heartbeat from {claim.runner}"
        minutes = (now - seen).total_seconds() / 60
        if minutes >= self.expiry_min:
            return f"expired: no heartbeat from {claim.runner} for {int(minutes)} min"
        return ""

    def blocker(self, email: str, run: str = "") -> str:
        """Why `run` may not send to this email right now ('' = free or ours)."""
        claims = self.claims.get(norm(email), [])
        for c in claims:
            if c.run != run and c.state in NEEDS_CHECK:
                return f"{c.state.lower()} by {c.runner} in '{c.campaign}' since {c.updated_at} (check Crisp)"
        top = owner([c for c in claims if not (c.run != run and self.expired(c))])
        if top is not None and top.run != run:
            return f"claimed by {top.runner} in '{top.campaign}'"
        for c in claims:
            why = sent_blocks(c, self.campaign, self.recent_days, self.now or sc.now_dhaka())
            if why and c.run != run:
                return why
        return ""

    def history(self) -> list[tuple]:
        """(email, time, campaign, result) for sends seen in the shared log.
        Sending and Unconfirmed count as 'possibly sent'."""
        out = []
        for email, claims in self.claims.items():
            for c in claims:
                if c.state == SENT:
                    out.append((email, c.updated_at, c.campaign, sc.SENT))
                elif c.state in NEEDS_CHECK:
                    out.append((email, c.updated_at, c.campaign, sc.UNCONFIRMED))
        return out

    def needs_check(self) -> list[Claim]:
        return [c for cl in self.claims.values() for c in cl if c.state in NEEDS_CHECK]

    def team(self) -> list[dict]:
        """One line per run for the Team panel: who, campaign, sent/claimed, last seen."""
        out = []
        now = self.now or sc.now_dhaka()
        per_run: dict = {}
        for cl in self.claims.values():
            for c in cl:
                per_run.setdefault(c.run, []).append(c)
        for run, row in self.runners.items():
            mine = per_run.get(run, [])
            seen = self.last_seen(run)
            age = int((now - seen).total_seconds() // 60) if seen else None
            state = row.get("State", "")
            if state == "Running" and (age is None or age >= self.expiry_min):
                state = "Not responding"
            out.append({"run": run, "runner": row.get("Runner", ""), "campaign": row.get("Campaign", ""),
                        "state": state, "last_seen_min": age,
                        "sent": sum(1 for c in mine if c.state == SENT),
                        "claimed": sum(1 for c in mine if c.state in (CLAIMED, SENDING)),
                        "needs_check": sum(1 for c in mine if c.state in NEEDS_CHECK)})
        return out


def to_dicts(values: list[list[str]], header: list[str]) -> list[dict]:
    rows = []
    for raw in (values[1:] if values else []):
        raw = list(raw) + [""] * len(header)
        rows.append({h: str(raw[i]).strip() for i, h in enumerate(header)})
    return rows


# ------------------------------------------------------------------ talking to the Sheet
class Claims:
    """One runner's view: claim a batch, check before Send, record results.

    `source` is a connected sheet_source.SheetSource (its pacer handles
    Google's rate limits and retries)."""

    def __init__(self, source, campaign: str, runner: str = "", run_id: str = "",
                 batch: int = DEFAULT_BATCH, expiry_min: int = DEFAULT_EXPIRY_MIN, clock=None,
                 recent_days: int = 0):
        self.src = source
        self.campaign = campaign
        self.runner = runner or runner_label()
        self.run = run_id or new_run_id()
        self.batch = max(1, int(batch or DEFAULT_BATCH))
        self.expiry_min = max(1, int(expiry_min or DEFAULT_EXPIRY_MIN))
        self.clock = clock or sc.now_dhaka
        self.recent_days = recent_days
        self.state = "Running"             # shown in the Team panel (Running / Paused / ...)
        self.mine: list[str] = []          # emails this run currently holds as Claimed
        self.sent = 0
        self.released_log: list[str] = []  # human-readable lines about releases
        self._ws: dict = {}
        self._runner_row = None
        self._beat_stop = threading.Event()
        self._write_lock = threading.Lock()

    # ---------------------------------------------------------- tabs
    def _tab(self, title: str, header: list[str], create: bool):
        if title in self._ws:
            return self._ws[title]
        p, sh = self.src.pacer, self.src.sh
        try:
            ws = p.call(sh.worksheet, title)
        except Exception:  # noqa: BLE001  (not found)
            if not create:
                return None
            ws = p.call(sh.add_worksheet, title=title, rows=1000, cols=len(header))
            p.call(ws.update, values=[header], range_name="A1", value_input_option="RAW")
            try:
                p.call(ws.hide)
            except Exception:  # noqa: BLE001
                pass
        self._ws[title] = ws
        return ws

    def _append(self, title: str, header: list[str], rows: list[list[str]]) -> None:
        ws = self._tab(title, header, create=True)
        with self._write_lock:
            self.src.pacer.call(ws.append_rows, rows, value_input_option="RAW",
                                insert_data_option="INSERT_ROWS", table_range="A1")

    def snapshot(self, create: bool = False) -> Snapshot:
        snap = Snapshot(now=self.clock(), expiry_min=self.expiry_min, campaign=self.campaign,
                        recent_days=self.recent_days)
        ws = self._tab(CLAIMS_TAB, CLAIM_HEADER, create)
        if ws is not None:
            snap.claims = replay(self.src.pacer.call(ws.get_all_values))
        rws = self._tab(RUNNERS_TAB, RUNNER_HEADER, create)
        if rws is not None:
            snap.runners = {r["Run"]: r for r in to_dicts(self.src.pacer.call(rws.get_all_values), RUNNER_HEADER)
                            if r.get("Run")}
        return snap

    def _row(self, email: str, state: str, note: str = "", run: str = "", runner: str = "",
             campaign: str = "") -> list[str]:
        return [sc.stamp(self.clock()), norm(email), state, runner or self.runner, run or self.run,
                campaign or self.campaign, note]

    # ---------------------------------------------------------- runner heartbeat
    def start(self, heartbeat: bool = True) -> None:
        now = sc.stamp(self.clock())
        self._append(RUNNERS_TAB, RUNNER_HEADER,
                     [[self.run, self.runner, self.campaign, now, now, "Running", "0", "0"]])
        self._tab(CLAIMS_TAB, CLAIM_HEADER, create=True)
        if heartbeat:
            threading.Thread(target=self._beat_loop, daemon=True).start()

    def _find_runner_row(self):
        if self._runner_row is None:
            ws = self._tab(RUNNERS_TAB, RUNNER_HEADER, create=True)
            col = self.src.pacer.call(ws.col_values, 1)
            hits = [i + 1 for i, v in enumerate(col) if str(v).strip() == self.run]
            self._runner_row = hits[0] if hits else None
        return self._runner_row

    def heartbeat(self, state: str = "") -> None:
        state = state or self.state
        row = self._find_runner_row()
        if row is None:
            return
        ws = self._ws[RUNNERS_TAB]
        with self._write_lock:
            self.src.pacer.call(ws.update, values=[[sc.stamp(self.clock()), state, str(self.sent),
                                                    str(len(self.mine))]],
                                range_name=f"E{row}", value_input_option="RAW")

    def _beat_loop(self):
        while not self._beat_stop.wait(HEARTBEAT_SECONDS):
            try:
                self.heartbeat()
            except Exception:  # noqa: BLE001  (a missed beat is retried next time)
                pass

    # ---------------------------------------------------------- claiming
    def claim_batch(self, candidates: list[str]) -> list[str]:
        """Claim up to `batch` free emails from candidates (in order). Returns the
        emails this run now owns. Never claims the whole list at once."""
        snap = self.snapshot(create=True)
        releases, wanted = [], []
        for email in candidates:
            e = norm(email)
            if not e or e in wanted or e in self.mine:
                continue
            claims = snap.claims.get(e, [])
            for c in claims:
                why = snap.expired(c) if c.run != self.run else ""
                if why:
                    releases.append(self._row(e, RELEASED, f"{why} (released by {self.runner})",
                                              run=c.run, runner=c.runner, campaign=c.campaign))
                    self.released_log.append(f"{e}: {why}")
                    c.state = RELEASED
            if any(c.state in ACTIVE and c.run != self.run for c in claims):
                continue                   # someone holds it, or it needs a check in Crisp
            if any(sent_blocks(c, self.campaign, self.recent_days, snap.now) for c in claims):
                continue                   # sent by another runner meanwhile
            wanted.append(e)
            if len(wanted) >= self.batch:
                break
        if releases:
            self._append(CLAIMS_TAB, CLAIM_HEADER, releases)
        if not wanted:
            return []
        self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(e, CLAIMED) for e in wanted])
        after = self.snapshot()
        won, lost = [], []
        for e in wanted:
            top = owner(after.claims.get(e, []))
            (won if top is not None and top.run == self.run else lost).append(e)
        if lost:
            self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(e, RELEASED, "lost: claimed first by another runner")
                                                     for e in lost])
        self.mine.extend(won)
        return won

    def still_mine(self, email: str) -> str:
        """'' if this run still owns the claim and may go on; else the reason."""
        snap = self.snapshot()
        claims = snap.claims.get(norm(email), [])
        top = owner(claims)
        me = next((c for c in claims if c.run == self.run), None)
        if me is None or me.state in FINAL:
            return "this run's claim was released" + (f" ({me.note})" if me and me.note else "")
        if top is None or top.run != self.run:
            return f"claimed by {top.runner} in '{top.campaign}'" if top else "claim not found"
        for c in claims:
            why = sent_blocks(c, self.campaign, self.recent_days, snap.now)
            if why and c.run != self.run:
                return why
        return ""

    def begin_send(self, email: str) -> None:
        """Just before the Send click: record Sending, then re-check. Raises if the
        claim was lost, so the click never happens."""
        self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(email, SENDING)])
        problem = self.still_mine(email)
        if problem:
            try:
                self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(email, RELEASED, f"not sent: {problem}")])
            except Exception:  # noqa: BLE001  (stays Sending: a person checks it; nothing was sent)
                pass
            if norm(email) in self.mine:
                self.mine.remove(norm(email))
            raise ClaimLost(problem)
        if norm(email) in self.mine:
            self.mine.remove(norm(email))    # Sending: never released by Stop or by anyone else

    def finish(self, email: str, result: str, note: str = "") -> None:
        state = {sc.SENT: SENT, sc.UNCONFIRMED: UNCONFIRMED}.get(result, FAILED)
        self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(email, state, note[:200])])
        if norm(email) in self.mine:
            self.mine.remove(norm(email))
        if state == SENT:
            self.sent += 1

    def release(self, email: str, note: str) -> None:
        """Give back one Claimed email (never one that reached Sending)."""
        e = norm(email)
        if e in self.mine:
            self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(e, RELEASED, note)])
            self.mine.remove(e)

    def release_all(self, note: str) -> int:
        """Stop / end of run: give back every claim that never reached Sending."""
        rows = [self._row(e, RELEASED, note) for e in self.mine]
        if rows:
            self._append(CLAIMS_TAB, CLAIM_HEADER, rows)
        n = len(rows)
        self.mine = []
        return n

    def close(self, state: str = "Finished") -> None:
        self._beat_stop.set()
        try:
            self.heartbeat(state)
        except Exception:  # noqa: BLE001
            pass

    def resolve_manually(self, email: str, run: str, was_sent: bool, who: str) -> None:
        """A person checked Crisp for a Sending/Unconfirmed claim."""
        snap = self.snapshot()
        claim = next((c for c in snap.claims.get(norm(email), []) if c.run == run), None)
        if claim is None:
            raise ValueError("Claim not found.")
        self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row(
            email, SENT if was_sent else RELEASED, f"manual: checked in Crisp by {who}",
            run=run, runner=claim.runner, campaign=claim.campaign)])

    # ---------------------------------------------------------- schema changes (the one real lock)
    def schema_lock(self, wait=None, tries: int = 30) -> bool:
        """Short lock for adding columns, using the same append-and-check trick.
        Returns True when held. Others wait (they are not refused)."""
        import time as _time
        wait = wait or _time.sleep
        for _attempt in range(tries):
            self._schema_n = getattr(self, "_schema_n", 0) + 1
            sub = f"{self.run}-s{self._schema_n}"   # a released claim id can never come back
            self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row("#schema", CLAIMED, "schema change", run=sub)])
            snap = self.snapshot()
            claims = snap.claims.get("#schema", [])
            now = self.clock()
            stale = [c for c in claims if c.run != sub and c.state == CLAIMED
                     and (parse_stamp(c.claimed_at) is None
                          or now - parse_stamp(c.claimed_at) > timedelta(minutes=SCHEMA_LOCK_MINUTES))]
            if stale:
                self._append(CLAIMS_TAB, CLAIM_HEADER, [
                    self._row("#schema", RELEASED, "expired schema lock", run=c.run, runner=c.runner,
                              campaign=c.campaign) for c in stale])
                for c in stale:
                    c.state = RELEASED
            top = owner(claims)
            if top is not None and top.run == sub:
                self._schema_run = sub
                return True
            self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row("#schema", RELEASED, "waiting for schema lock",
                                                              run=sub)])
            wait(3)
        return False

    def schema_unlock(self) -> None:
        sub = getattr(self, "_schema_run", "")
        if sub:
            self._append(CLAIMS_TAB, CLAIM_HEADER, [self._row("#schema", RELEASED, "schema change done", run=sub)])
            self._schema_run = ""


class ClaimLost(RuntimeError):
    """The claim was lost before the Send click; nothing was sent."""


def read_raw(source) -> dict:
    """Raw claims/runners tab values (GUI preview cache). Creates nothing."""
    out = {}
    for key, title in (("claims", CLAIMS_TAB), ("runners", RUNNERS_TAB)):
        try:
            ws = source.pacer.call(source.sh.worksheet, title)
        except Exception:  # noqa: BLE001  (tab not created yet)
            out[key] = []
            continue
        out[key] = source.pacer.call(ws.get_all_values)
    return out


def snapshot_from_raw(raw: dict, campaign: str = "", expiry_min: int = DEFAULT_EXPIRY_MIN,
                      recent_days: int = 0, now=None) -> Snapshot:
    snap = Snapshot(now=now or sc.now_dhaka(), expiry_min=expiry_min, campaign=campaign,
                    recent_days=recent_days)
    snap.claims = replay(raw.get("claims") or [])
    snap.runners = {r["Run"]: r for r in to_dicts(raw.get("runners") or [], RUNNER_HEADER) if r.get("Run")}
    return snap


def read_snapshot(source, campaign: str = "", expiry_min: int = DEFAULT_EXPIRY_MIN,
                  recent_days: int = 0) -> Snapshot:
    """Read-only (dry runs, GUI preview, Team panel). Creates nothing."""
    return Claims(source, campaign, expiry_min=expiry_min, recent_days=recent_days).snapshot(create=False)

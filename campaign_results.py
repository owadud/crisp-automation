"""Campaign Results: statuses read from Crisp conversation pages (read-only).

Based on real Crisp markup captured with tools/crisp_status_probe.py:
- every message: .c-conversation-box-content-message  (--operator = us, --user = customer,
  --event = e.g. data-event "state:resolved", --note = internal note)
- its time: .c-base-tooltip__main  "Sent, 9h (10:15 AM)" / "Received, Yesterday (9:02 PM)"
- delivery marker under our latest message: .c-conversation-box-content-message__status
  label "Delivered to email" / "Read in email" (icon class ...__icon--delivered / --read)

Crisp shows exact SENT and REPLY times. It does NOT show delivered/read times, so those
are stored as the time we first saw them, flagged "first seen".

Pure Python (no Qt, no Playwright) so it is shared by the agent and the GUI and tested
without a browser. Data lives in <app folder>/campaign_results.json (never uploaded).
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

# ------------------------------------------------------------------ statuses
UNCONFIRMED, SENT, FAILED, DELIVERED, READ, REPLIED = (
    "Unconfirmed", "Sent", "Failed", "Delivered", "Read", "Replied")
RANK = {UNCONFIRMED: 0, SENT: 1, FAILED: 2, DELIVERED: 3, READ: 4, REPLIED: 5}
STATUSES = [SENT, DELIVERED, READ, REPLIED, FAILED, UNCONFIRMED]
READ_NOTE = "Read is approximate: email open tracking (mail apps can auto-open or block images)."

# Runs in the Crisp page. Returns only statuses, times, markers and event types:
# never message text, names or email addresses.
READ_JS = r"""
() => {
  const t = el => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
  const href = location.href;
  const out = {url: href, login_lost: false, messages: [], has_box: false};
  if (!/\/website\/[^/]+\/inbox\//.test(href) || document.querySelector('input[type="password"]')) {
    out.login_lost = true;
    return out;
  }
  out.has_box = !!document.querySelector('[class*="c-conversation-box-content"]');
  for (const m of document.querySelectorAll('.c-conversation-box-content-message')) {
    const c = m.className || '';
    const dir = /--operator/.test(c) ? 'operator' : (/--user/.test(c) ? 'user' : 'other');
    const kind = /--event/.test(c) ? 'event' : (/--note/.test(c) ? 'note' : 'message');
    const tip = m.querySelector('.c-conversation-box-content-message-bubble__tooltip');
    const main = t(tip && tip.querySelector('.c-base-tooltip__main'));
    const when = main || t(tip && tip.querySelector('.c-base-tooltip__default'));
    const ev = m.querySelector('[data-event]');
    const st = m.querySelector('.c-conversation-box-content-message__status');
    const icon = st && st.querySelector('[class*="c-conversation-box-content-message__icon--"]');
    const iconCls = icon ? ((icon.getAttribute('class') || '').match(/__icon--([a-z_-]+)/) || [])[1] || '' : '';
    out.messages.push({dir, kind, when, event: ev ? (ev.getAttribute('data-event') || '') : '',
                       marker: t(st && st.querySelector('.c-conversation-box-content-message__label')),
                       marker_kind: iconCls});
  }
  return out;
}
"""

_CLOCK = r"\((\d{1,2}):(\d{2})\s*([AaPp][Mm])?\)"
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _clock(m_hour, m_min, ampm) -> tuple[int, int]:
    h, mi = int(m_hour), int(m_min)
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and h != 12:
            h += 12
        if ampm == "am" and h == 12:
            h = 0
    return h, mi


def parse_when(text: str, now: datetime) -> tuple[datetime | None, str]:
    """Crisp time text -> (datetime, kind). kind: 'exact' (clock shown) or 'approx' ('13m').

    Accepts 'Sent, 9h (10:15 AM)', 'Received, Yesterday (9:02 PM)', '13m', 'Today (10:15)',
    '9 Nov (9:15 PM)', 'Nov 9 (9:15 PM)', '9 Nov 2025 (9:15 PM)'.
    """
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if "," in s and re.match(r"^[A-Za-z ]+,", s):
        s = s.split(",", 1)[1].strip()
    if not s:
        return None, ""
    if s.lower() in ("now", "just now"):
        return now, "approx"
    m = re.fullmatch(r"(\d+)\s*([smhd])", s)
    if m:
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        return now - timedelta(**{unit: int(m.group(1))}), "approx"
    clock = re.search(_CLOCK, s)
    if not clock:
        return None, ""
    h, mi = _clock(*clock.groups())
    head = s[:clock.start()].strip().lower()
    base = None
    m = re.fullmatch(r"(\d+)\s*([smhd])", head)
    if m:
        unit = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}[m.group(2)]
        base = now - timedelta(**{unit: int(m.group(1))})
    elif head in ("", "today"):
        base = now
    elif head == "yesterday":
        base = now - timedelta(days=1)
    else:
        m = (re.fullmatch(r"(\d{1,2}) ([a-z]{3})[a-z]*\.?(?: (\d{4}))?", head)
             or re.fullmatch(r"([a-z]{3})[a-z]*\.? (\d{1,2})(?:,? (\d{4}))?", head))
        if m:
            g = m.groups()
            day, mon = (int(g[0]), g[1]) if g[0].isdigit() else (int(g[1]), g[0])
            if mon in _MONTHS:
                year = int(g[2]) if g[2] else now.year
                try:
                    base = datetime(year, _MONTHS[mon], day)
                except ValueError:
                    return None, ""
                if not g[2] and base.date() > now.date():
                    base = base.replace(year=year - 1)
    if base is None:
        return None, ""
    dt = base.replace(hour=h, minute=mi, second=0, microsecond=0)
    if dt > now + timedelta(minutes=5) and head and head[-1] in "smhd":
        dt -= timedelta(days=1)   # "9h (11:50 PM)" seen just after midnight
    return dt, "exact"


def fmt(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else ""


def analyze(raw: dict, sent_at: str = "", now: datetime | None = None) -> dict:
    """One conversation page -> {status, reply_at, reason, open, problem}.

    'problem' is set when the page cannot be trusted: 'login_lost' or 'layout'.
    """
    now = now or datetime.now()
    if raw.get("login_lost"):
        return {"problem": "login_lost"}
    msgs = raw.get("messages") or []
    if not msgs or not any(m.get("when") for m in msgs):
        return {"problem": "layout"}
    ours = [i for i, m in enumerate(msgs) if m["dir"] == "operator" and m["kind"] == "message"]
    if not ours:
        return {"problem": "no_our_message"}
    target = ours[-1]
    try:
        sent_dt = datetime.strptime(sent_at[:16], "%Y-%m-%d %H:%M") if sent_at else None
    except ValueError:
        sent_dt = None
    if sent_dt:
        timed = [(i, parse_when(msgs[i]["when"], now)[0]) for i in ours]
        timed = [(i, dt) for i, dt in timed if dt]
        if timed:
            target = min(timed, key=lambda x: abs((x[1] - sent_dt).total_seconds()))[0]
    status, reason = SENT, ""
    for i in ours:
        if i < target:
            continue
        marker = (msgs[i].get("marker") or "").lower()
        mk = (msgs[i].get("marker_kind") or "").lower()
        if re.search(r"fail|error|bounce|not delivered|undeliver|reject|block", marker + " " + mk):
            if i == target:
                status, reason = FAILED, msgs[i].get("marker") or "not delivered"
        elif "read" in mk or marker.startswith("read"):
            if i == target:
                status = READ
            elif RANK[status] < RANK[DELIVERED]:
                status = DELIVERED       # a later message was read -> ours got through
        elif "deliver" in mk or "deliver" in marker:
            if RANK[status] < RANK[DELIVERED]:
                status = DELIVERED
    reply_at, reply_kind = None, ""
    for m in msgs[target + 1:]:
        if m["dir"] == "user" and m["kind"] == "message":
            reply_at, reply_kind = parse_when(m["when"], now)
            status = REPLIED
            break
    # open/resolved: the last state event vs. the last customer message
    open_ = None
    last_user = max((i for i, m in enumerate(msgs) if m["dir"] == "user" and m["kind"] == "message"), default=-1)
    states = [(i, m["event"]) for i, m in enumerate(msgs) if m["kind"] == "event" and m["event"].startswith("state:")]
    if states:
        i, ev = states[-1]
        open_ = not (ev == "state:resolved" and i > last_user)
    elif last_user >= 0:
        open_ = True
    return {"status": status, "reply_at": fmt(reply_at), "reply_kind": reply_kind,
            "reason": reason, "open": open_, "problem": ""}


# ------------------------------------------------------------------ store
def rkey(campaign: str, email: str) -> str:
    return f"{str(campaign).strip().lower()}|{str(email).strip().lower()}"


class ResultsStore:
    """<data dir>/campaign_results.json: per-recipient results and check history."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.recipients: dict = {}
        self.checks: list = []
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.recipients, self.checks = data.get("recipients", {}), data.get("checks", [])
        except (OSError, ValueError):
            self.recipients, self.checks = {}, []

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"recipients": self.recipients, "checks": self.checks}, indent=1,
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # -------------------------------------------------------------- seeding
    def sync_history(self, history_rows) -> int:
        """Add real sends from history.csv (live Sent/Unconfirmed). Returns how many were new."""
        added = 0
        for r in history_rows:
            if r.get("Mode") != "live" or r.get("Result") not in (SENT, UNCONFIRMED):
                continue
            campaign = (r.get("Campaign") or r.get("Subject") or "(no campaign)").strip()
            email = str(r.get("Email", "")).strip().lower()
            if not email:
                continue
            key = rkey(campaign, email)
            rec = self.recipients.get(key)
            if rec is None:
                rec = self.recipients[key] = {
                    "campaign": campaign, "email": email, "name": r.get("Name", ""),
                    "shortcut": r.get("Shortcut", ""), "version": r.get("Version", ""),
                    "sent_at": str(r.get("Time", ""))[:16], "link": r.get("Link", ""),
                    "status": r["Result"], "delivered_at": "", "delivered_seen": False,
                    "read_at": "", "read_seen": False, "reply_at": "", "reply_seen": False,
                    "reason": "", "open": None, "last_checked": "", "note": ""}
                added += 1
            elif not rec.get("link") and r.get("Link"):
                rec["link"] = r["Link"]
        return added

    # -------------------------------------------------------------- updates
    def apply(self, key: str, result: dict, now: datetime | None = None) -> None:
        """Merge one observation. Never downgrades; first-seen times are flagged."""
        rec = self.recipients[key]
        now_s = fmt(now or datetime.now())
        rec["last_checked"] = now_s
        if result.get("problem"):
            rec["note"] = {"no_our_message": "our message not found in the conversation"}.get(
                result["problem"], result["problem"])
            return
        rec["note"] = ""
        new = result.get("status", SENT)
        if new == FAILED and RANK.get(rec["status"], 0) < RANK[DELIVERED]:
            rec["status"], rec["reason"] = FAILED, result.get("reason", "")
        elif RANK.get(new, 0) > RANK.get(rec["status"], 0) and new != FAILED:
            rec["status"] = new
        rank = RANK.get(rec["status"], 0)
        if rank >= RANK[DELIVERED] and rec["status"] != FAILED and not rec["delivered_at"]:
            rec["delivered_at"], rec["delivered_seen"] = now_s, True
        if rank >= RANK[READ] and not rec["read_at"] and new in (READ, REPLIED):
            if new == READ or result.get("reply_at"):
                rec["read_at"], rec["read_seen"] = now_s, True
        if new == REPLIED and not rec["reply_at"]:
            if result.get("reply_at"):
                rec["reply_at"], rec["reply_seen"] = result["reply_at"], result.get("reply_kind") != "exact"
            else:
                rec["reply_at"], rec["reply_seen"] = now_s, True
        if result.get("open") is not None:
            rec["open"] = result["open"]

    def record_check(self, campaign: str, now: datetime | None = None) -> dict:
        snap = {"campaign": campaign, "at": fmt(now or datetime.now()), **summary(self.rows(campaign))}
        self.checks.append(snap)
        return snap

    # -------------------------------------------------------------- queries
    def campaigns(self) -> list[str]:
        names = {}
        for r in self.recipients.values():
            names.setdefault(r["campaign"], r["sent_at"])
            names[r["campaign"]] = min(names[r["campaign"]], r["sent_at"])
        return sorted(names, key=lambda c: names[c], reverse=True)

    def rows(self, campaign: str = "") -> list[dict]:
        c = campaign.strip().lower()
        return [r for r in self.recipients.values() if not c or r["campaign"].strip().lower() == c]

    def last_checked(self, campaign: str = "") -> str:
        times = [r["last_checked"] for r in self.rows(campaign) if r.get("last_checked")]
        return max(times) if times else ""

    def history(self, campaign: str = "") -> list[dict]:
        c = campaign.strip().lower()
        return [h for h in self.checks if not c or h["campaign"].strip().lower() == c]


def summary(rows) -> dict:
    """Cumulative counts: replied people also count as read and delivered."""
    sent = len(rows)
    rank = [RANK.get(r["status"], 0) for r in rows]
    failed = sum(1 for r in rows if r["status"] == FAILED)
    out = {"sent": sent,
           "delivered": sum(1 for r, k in zip(rows, rank) if k >= RANK[DELIVERED]),
           "read": sum(1 for k in rank if k >= RANK[READ]),
           "replied": sum(1 for r in rows if r["status"] == REPLIED),
           "failed": failed,
           "unconfirmed": sum(1 for r in rows if r["status"] == UNCONFIRMED)}
    for k in ("delivered", "read", "replied"):
        out[f"{k}_pct"] = round(100.0 * out[k] / sent, 1) if sent else 0.0
    return out


def summary_text(s: dict) -> str:
    return (f"{s['sent']} sent · {s['delivered']} delivered ({s['delivered_pct']}%) · "
            f"{s['read']} read ({s['read_pct']}%, approx.) · {s['replied']} replied ({s['replied_pct']}%) · "
            f"{s['failed']} failed")


def compare(rows, by: str) -> list[dict]:
    """Group rows by 'campaign' or 'version' -> one summary per group."""
    groups: dict = {}
    for r in rows:
        groups.setdefault(r.get(by) or "(none)", []).append(r)
    return [{by: k, **summary(v)} for k, v in sorted(groups.items())]


def recheck_suggestions(rows, last_checked: str, now: datetime | None = None) -> list[str]:
    """'1 hour' / '1 day' / '3 days' after the first send, if not checked since."""
    now = now or datetime.now()
    sent = sorted(r["sent_at"] for r in rows if r.get("sent_at"))
    if not sent:
        return []
    try:
        first = datetime.strptime(sent[0][:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return []
    out = []
    for label, delta in (("1 hour", timedelta(hours=1)), ("1 day", timedelta(days=1)), ("3 days", timedelta(days=3))):
        due = first + delta
        if now >= due and (not last_checked or last_checked < fmt(due)):
            out.append(label)
    return out[-1:]   # the latest one that is due


TABLE_COLUMNS = ["Email", "Name", "Status", "Sent", "Delivered", "Read", "Replied", "Shortcut version",
                 "Campaign", "Conversation link", "Note"]


def table_row(r: dict) -> list[str]:
    def seen(value, flag):
        return f"{value} (first seen)" if value and flag else value
    status = r["status"] + (f": {r['reason']}" if r["status"] == FAILED and r.get("reason") else "")
    return [r["email"], r.get("name", ""), status, r.get("sent_at", ""),
            seen(r.get("delivered_at", ""), r.get("delivered_seen")),
            seen(r.get("read_at", ""), r.get("read_seen")),
            seen(r.get("reply_at", ""), r.get("reply_seen")),
            r.get("version", ""), r["campaign"], r.get("link", ""),
            r.get("note") or ("" if r.get("link") else "no conversation link recorded")]


def export_csv(rows, path: Path) -> Path:
    with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
        w = csv.writer(handle)
        w.writerow(TABLE_COLUMNS)
        for r in rows:
            w.writerow(table_row(r))
    return Path(path)

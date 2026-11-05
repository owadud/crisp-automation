"""Campaigns as named objects, plus the run queue. No Qt here (easy to test).

A campaign = name + its own list/source, subject, shortcut(s), segment and
settings, a status and its own results (history rows carry its name).
Stored in <data_dir>/campaigns.json, written atomically.

Queue rules (the safe default runs ONE campaign at a time per browser):
- Campaigns run in queue order. Paused campaigns are skipped until resumed.
- A failure (error, stop on failure, unconfirmed send) halts the whole queue.
- Stopping a campaign affects only that campaign.
- On the next start, queued and paused campaigns are kept; a campaign that
  was running becomes "unconfirmed" (an email may have been mid-send) and is
  never resumed automatically. The queue never starts by itself.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import send_control as sc  # noqa: E402

DRAFT, READY, QUEUED, RUNNING, PAUSED, FINISHED, STOPPED, FAILED, UNCONFIRMED = (
    "draft", "ready", "queued", "running", "paused", "finished", "stopped", "failed", "unconfirmed")
STATUS_LABELS = {DRAFT: "Draft", READY: "Ready", QUEUED: "Queued", RUNNING: "Running", PAUSED: "Paused",
                 FINISHED: "Finished", STOPPED: "Stopped", FAILED: "Failed",
                 UNCONFIRMED: "Unconfirmed: check Crisp"}

# Settings that belong to the WHOLE app, never to one campaign (so several
# campaigns can never multiply the sending speed).
APP_WIDE = ("delay", "random_delay", "daily_cap")
# Settings that are not part of a campaign at all.
NOT_SAVED = APP_WIDE + ("data_dir", "runner_name", "claim_batch", "claim_expiry", "retry_only",
                        "history_file", "run_dir")
REQUIRED = ("subject", "shortcut", "segment")

SEND_SECONDS = 20.0   # rough time Crisp needs per email (open, fill, insert, send)


@dataclass
class Campaign:
    id: str
    name: str
    settings: dict = field(default_factory=dict)
    status: str = DRAFT
    created: str = ""
    updated: str = ""
    note: str = ""                 # why it stopped / what to check
    last_run_dir: str = ""
    last: dict = field(default_factory=dict)   # sent / failed / skipped / elsewhere / eligible of the last run

    @property
    def label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def missing(self) -> list[str]:
        s = self.settings
        out = [k for k in REQUIRED if not str(s.get(k, "")).strip()]
        if s.get("source") == "sheet":
            out += [k for k in ("sheet_url", "sheet_tab", "google_key") if not str(s.get(k, "")).strip()]
        elif not str(s.get("csv", "")).strip():
            out.append("csv")
        return out


def clean_settings(values: dict) -> dict:
    return {k: str(v) for k, v in values.items() if k not in NOT_SAVED}


class CampaignStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.campaigns: dict[str, Campaign] = {}
        self.queue: list[str] = []      # campaign ids in run order
        self.load()

    # ---------------------------------------------------------------- disk
    def load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        self.campaigns = {}
        for raw in data.get("campaigns", []):
            try:
                c = Campaign(**{k: raw[k] for k in raw if k in Campaign.__dataclass_fields__})
            except TypeError:
                continue
            self.campaigns[c.id] = c
        self.queue = [i for i in data.get("queue", []) if i in self.campaigns]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        data = {"campaigns": [asdict(c) for c in self.campaigns.values()], "queue": self.queue}
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # ---------------------------------------------------------------- objects
    def list(self) -> list[Campaign]:
        """Queue order first, then the rest by name."""
        queued = [self.campaigns[i] for i in self.queue]
        rest = sorted((c for c in self.campaigns.values() if c.id not in self.queue), key=lambda c: c.name.lower())
        return queued + rest

    def get(self, cid: str) -> Campaign | None:
        return self.campaigns.get(cid)

    def by_name(self, name: str) -> Campaign | None:
        key = str(name or "").strip().lower()
        return next((c for c in self.campaigns.values() if c.name.strip().lower() == key), None)

    def add(self, name: str, values: dict) -> Campaign:
        name = str(name or "").strip() or "Untitled campaign"
        base, n = name, 2
        while self.by_name(name):
            name, n = f"{base} ({n})", n + 1
        now = sc.stamp()
        c = Campaign(uuid.uuid4().hex[:8], name, clean_settings(values), created=now, updated=now)
        c.settings["campaign"] = name
        c.status = DRAFT if c.missing() else READY
        self.campaigns[c.id] = c
        self.save()
        return c

    def update(self, cid: str, values: dict) -> Campaign:
        c = self.campaigns[cid]
        if c.status == RUNNING:
            raise ValueError("A running campaign cannot be changed.")
        c.settings = clean_settings(values)
        c.settings["campaign"] = c.name
        c.updated = sc.stamp()
        if c.status in (DRAFT, READY):
            c.status = DRAFT if c.missing() else READY
        self.save()
        return c

    def rename(self, cid: str, name: str) -> None:
        name = str(name or "").strip()
        other = self.by_name(name)
        if not name or (other and other.id != cid):
            raise ValueError("Use a name that no other campaign has.")
        c = self.campaigns[cid]
        c.name = name
        c.settings["campaign"] = name
        self.save()

    def duplicate(self, cid: str) -> Campaign:
        src = self.campaigns[cid]
        return self.add(f"{src.name} copy", dict(src.settings))

    def remove(self, cid: str) -> None:
        if self.campaigns[cid].status == RUNNING:
            raise ValueError("Stop the campaign before removing it.")
        self.campaigns.pop(cid)
        self.queue = [i for i in self.queue if i != cid]
        self.save()

    def set_status(self, cid: str, status: str, note: str = "") -> None:
        c = self.campaigns[cid]
        c.status, c.note = status, note
        if status not in (QUEUED, PAUSED, RUNNING) and cid in self.queue:
            self.queue.remove(cid)
        self.save()

    # ---------------------------------------------------------------- queue
    def enqueue(self, cid: str) -> None:
        c = self.campaigns[cid]
        if c.missing():
            raise ValueError(f"'{c.name}' is missing: {', '.join(c.missing())}.")
        if c.status == UNCONFIRMED:
            raise ValueError(f"'{c.name}' may have an unconfirmed send. Check Crisp, then mark it checked.")
        if c.status == RUNNING:
            return
        if cid not in self.queue:
            self.queue.append(cid)
        c.status, c.note = QUEUED, ""
        self.save()

    def dequeue(self, cid: str) -> None:
        c = self.campaigns[cid]
        if c.status == RUNNING:
            raise ValueError("Stop the campaign first.")
        if cid in self.queue:
            self.queue.remove(cid)
        if c.status in (QUEUED, PAUSED):
            c.status = DRAFT if c.missing() else READY
        self.save()

    def move(self, cid: str, step: int) -> None:
        if cid not in self.queue:
            return
        i = self.queue.index(cid)
        j = max(0, min(len(self.queue) - 1, i + step))
        self.queue.insert(j, self.queue.pop(i))
        self.save()

    def run_next(self, cid: str) -> None:
        """Put it first among the campaigns that are not running."""
        self.enqueue(cid)
        self.queue.remove(cid)
        running = [i for i in self.queue if self.campaigns[i].status == RUNNING]
        self.queue.insert(len(running), cid)
        self.save()

    def pause(self, cid: str) -> None:
        """A queued campaign is skipped until resumed. (A running one is paused by
        the runner between two recipients; its status is set by the caller.)"""
        c = self.campaigns[cid]
        if c.status == QUEUED:
            c.status = PAUSED
            self.save()

    def resume(self, cid: str) -> None:
        c = self.campaigns[cid]
        if c.status == PAUSED:
            if cid not in self.queue:
                self.queue.append(cid)
            c.status = QUEUED
            self.save()

    def mark_checked(self, cid: str) -> None:
        """A person checked Crisp after an unconfirmed send."""
        c = self.campaigns[cid]
        if c.status == UNCONFIRMED:
            c.status, c.note = (DRAFT if c.missing() else READY), "Checked in Crisp."
            self.save()

    def running(self) -> list[Campaign]:
        return [c for c in self.campaigns.values() if c.status == RUNNING]

    def next_queued(self) -> Campaign | None:
        return next((self.campaigns[i] for i in self.queue if self.campaigns[i].status == QUEUED), None)

    def start(self, cid: str, run_dir: str = "") -> None:
        c = self.campaigns[cid]
        c.status, c.note, c.last_run_dir = RUNNING, "", run_dir
        self.save()

    def finish(self, cid: str, outcome: str, note: str = "", last: dict | None = None) -> None:
        """outcome: finished / stopped / failed / unconfirmed / paused."""
        c = self.campaigns.get(cid)
        if c is None:
            return
        if last is not None:
            c.last = dict(last)
        c.status, c.note = outcome, note
        if outcome != PAUSED and cid in self.queue:
            self.queue.remove(cid)
        self.save()

    def restore(self) -> list[str]:
        """After a restart. Returns messages to show. Never starts anything."""
        notes = []
        for c in self.campaigns.values():
            if c.status == RUNNING:
                c.status = UNCONFIRMED
                c.note = ("The app closed while this campaign was running. The last email may or may not have "
                          "been sent: check Crisp, then mark it checked.")
                if c.id in self.queue:
                    self.queue.remove(c.id)
                notes.append(f"'{c.name}' was running when the app closed: check Crisp before running it again.")
        queued = [c for c in self.list() if c.status in (QUEUED, PAUSED)]
        if queued:
            notes.append(f"{len(queued)} queued or paused campaign(s) restored. Nothing starts until you click "
                         "Run queue.")
        self.save()
        return notes


# ------------------------------------------------------------------ queue summary
def queue_summary(campaigns: list[Campaign], plans: dict, delay: float, daily_cap: int | None = None,
                  sent_today: int = 0) -> dict:
    """Numbers for the one confirmation before the queue starts.

    plans: {campaign id: (emails to send in file order, duplicates skipped, note)}.
    A person in several campaigns is counted (and sent) once: the first campaign
    in queue order gets them; the others skip them (the queue keeps the
    "emailed recently" rule at 1 day or more)."""
    seen: dict[str, str] = {}
    rows, total, overlaps = [], 0, {}
    for c in campaigns:
        emails, dups, note = plans.get(c.id, ([], 0, "not counted"))
        mine = 0
        for e in emails:
            key = str(e or "").strip().lower()
            if key in seen:
                overlaps.setdefault(key, [seen[key]]).append(c.name)
                continue
            seen[key] = c.name
            mine += 1
        rows.append({"id": c.id, "name": c.name, "subject": c.settings.get("subject", ""),
                     "shortcut": c.settings.get("shortcut", ""), "to_send": mine, "listed": len(emails),
                     "duplicates": dups, "overlap": len(emails) - mine, "note": note})
        total += mine
    capped = total
    if daily_cap is not None:
        capped = min(total, max(0, daily_cap - sent_today))
    seconds = capped * (SEND_SECONDS + max(0.0, float(delay or 0)))
    return {"rows": rows, "total": total, "capped": capped, "overlaps": overlaps, "seconds": seconds}


def duration_text(seconds: float) -> str:
    minutes = int(round(seconds / 60))
    if minutes < 60:
        return f"about {max(1, minutes)} min"
    return f"about {minutes // 60} h {minutes % 60:02d} min"

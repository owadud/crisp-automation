"""Turn the agent's console output into an accurate run state.

This module has no Qt dependency so it can be unit tested on its own.
Every rule here matches a line that crisp_email_agent.py actually prints;
nothing is counted as "sent" unless the agent printed
"Email sent successfully.", which it only does after Crisp cleared the
composer (see send_message() in the agent).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

EVENT_PREFIX = "@@EVENT"

# Text lines whose meaning (counts, positions) is carried by an event too.
# Once the agent has sent any event, these lines only move the step label,
# so nothing is counted twice. Without events, the text rules below apply.
_COVERED = re.compile(
    r"^(Eligible recipients:|\[\d+/\d+\]|SUCCESS\.$|Email sent successfully\.$|FAILED:|"
    r"Email was not sent\.$|SKIPPED:|RETRY:|Completed:|Failed:|Skipped:|No eligible recipients\.$|"
    r"Send window .* has ended|HEALTH (OK|FAIL):)")


# Phases of a run, in the order they normally occur.
IDLE = "idle"
STARTING = "starting"
WAITING_LOGIN = "waiting_login"
RUNNING = "running"
STOPPING = "stopping"
FINISHED = "finished"   # ended, no failures
FAILED = "failed"       # ended with a failure or error
STOPPED = "stopped"     # ended because the user stopped it

ACTIVE_PHASES = {STARTING, WAITING_LOGIN, RUNNING, STOPPING}


# (substring, fraction of one recipient done, human label)
# Fractions only ever move forward within a recipient.
_STEPS = (
    ("Recipient:", 0.05, "Opening new conversation"),
    ("Email suggestion selected", 0.15, "Recipient selected"),
    ("Subject menu rows", 0.20, "Choosing subject"),
    ("Subject chosen from dropdown", 0.30, "Subject selected"),
    ("Create Conversation clicked", 0.40, "Creating conversation"),
    ("New-conversation dialog closed", 0.45, "Conversation created"),
    ("Opening shortcut picker", 0.50, "Opening shortcuts"),
    ("Typing shortcut", 0.55, "Inserting shortcut"),
    ("Shortcut inserted successfully", 0.65, "Shortcut inserted"),
    ("Finding Send button", 0.70, "Finding Send button"),
    ("Clicking Send", 0.75, "Sending"),
    ("[DRY RUN] Send skipped", 0.90, "Dry run: send skipped"),
    ("Email sent successfully", 0.85, "Sent, confirmed by Crisp"),
    ("Mail Sent marked TRUE", 0.88, "CSV updated"),
    ("Adding segment", 0.90, "Adding segment"),
    ("Resolving conversation", 0.96, "Resolving conversation"),
)


@dataclass
class RunState:
    dry_run: bool = True
    phase: str = IDLE
    eligible: int = 0                 # recipients the agent will process
    position: int = 0                 # 1-based index of current recipient
    current_email: str = ""
    current_name: str = ""
    step: str = ""
    step_fraction: float = 0.0
    completed: int = 0                # recipients that reached "SUCCESS."
    sent_confirmed: int = 0           # real sends Crisp confirmed
    failed: int = 0
    skipped: int = 0                  # failed BEFORE Send and skipped (opt-in)
    retry: list = field(default_factory=list)   # (email, name, reason) not sent
    precheck: str = ""                # "Skipped before start: ..." from the agent
    warnings: int = 0
    last_error: str = ""
    unconfirmed_send: bool = False    # a send may have gone out unconfirmed
    stop_requested: bool = False
    exit_code: int | None = None
    summary_seen: bool = False
    no_eligible: bool = False
    history: list = field(default_factory=list)  # (email, outcome)
    health: list = field(default_factory=list)   # (check, ok, detail) from "Check Crisp"
    events_seen: bool = False

    # -------------------------------------------------------------- derived
    @property
    def active(self) -> bool:
        return self.phase in ACTIVE_PHASES

    @property
    def processed(self) -> int:
        return self.completed + self.failed + self.skipped

    @property
    def progress(self) -> float:
        """Overall progress 0..1 based on real recipient count."""
        if self.phase in (FINISHED,) and (self.eligible == 0 or self.no_eligible):
            return 1.0
        if self.eligible <= 0:
            return 0.0
        in_flight = self.step_fraction if self.position > self.processed else 0.0
        value = (self.processed + in_flight) / self.eligible
        return max(0.0, min(1.0, value))


class RunParser:
    """Feed raw bytes from the agent; complete lines update ``state``.

    Output arrives in arbitrary chunks, so bytes are buffered until a full
    line (and full UTF-8 character) is available. Without this, a line like
    "Email sent successfully." split over two reads would be missed.
    """

    def __init__(self, dry_run: bool = True):
        import codecs

        self.state = RunState(dry_run=dry_run, phase=STARTING)
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._buffer = ""

    # ------------------------------------------------------------- feeding
    def feed(self, data: bytes) -> list[str]:
        """Add raw output; return the complete lines that were processed."""
        self._buffer += self._decoder.decode(data)
        self._buffer = self._buffer.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self.handle_line(line)
        return lines

    def flush(self) -> list[str]:
        """Process any trailing text without a newline (at process exit)."""
        self._buffer += self._decoder.decode(b"", final=True)
        rest, self._buffer = self._buffer, ""
        if rest:
            self.handle_line(rest)
            return [rest]
        return []

    # ------------------------------------------------------------- parsing
    def handle_line(self, raw: str) -> None:
        s = self.state
        line = raw.strip()
        if not line:
            return

        if line.startswith(EVENT_PREFIX):
            try:
                event = json.loads(line[len(EVENT_PREFIX):].strip())
            except ValueError:
                return
            if isinstance(event, dict):
                s.events_seen = True
                self.handle_event(event)
            return

        if s.events_seen and _COVERED.match(line):
            self._advance(line)
            return

        m = re.match(r"^HEALTH (OK|FAIL):\s*(.*?):\s*(.*)$", line)
        if m:
            s.health.append((m.group(2), m.group(1) == "OK", m.group(3)))
            return

        if line.startswith("Dry run:"):
            s.dry_run = line.split(":", 1)[1].strip().lower() == "true"
            return

        m = re.match(r"^Eligible recipients:\s*(\d+)$", line)
        if m:
            s.eligible = int(m.group(1))
            return

        if line == "No eligible recipients.":
            s.no_eligible = True
            s.step = "No eligible recipients in the CSV"
            return

        if line.startswith("Waiting for Crisp Inbox login") or line.startswith("Still waiting for Crisp login"):
            if s.phase in (STARTING, WAITING_LOGIN):
                s.phase = WAITING_LOGIN
                s.step = "Waiting for Crisp login"
            return

        if line.startswith("Launching Chromium"):
            s.step = "Launching browser"
            return

        if line.startswith("Crisp Inbox detected"):
            if s.phase != STOPPING:
                s.phase = RUNNING
            s.step = "Signed in to Crisp"
            return

        m = re.match(r"^\[(\d+)/(\d+)\]\s+(.*?)\s*\|\s*(.*)$", line)
        if m:
            s.position = int(m.group(1))
            s.eligible = int(m.group(2))
            s.current_email = m.group(3).strip()
            s.current_name = m.group(4).strip()
            s.step = "Starting recipient"
            s.step_fraction = 0.0
            if s.phase not in (STOPPING,):
                s.phase = RUNNING
            return

        if line.startswith("Stop requested"):
            s.stop_requested = True
            s.step = "Stopped before the next recipient"
            return

        if line == "SUCCESS.":
            s.completed += 1
            s.step_fraction = 1.0
            s.step = "Dry run passed" if s.dry_run else "Completed"
            s.history.append((s.current_email, "dry-run" if s.dry_run else "sent"))
            return

        if line == "Email sent successfully.":
            s.sent_confirmed += 1
            self._advance(line)
            return

        if line.startswith("FAILED:"):
            s.failed += 1
            s.last_error = line[len("FAILED:"):].strip()
            if "MAY already have been sent" in line:
                s.unconfirmed_send = True
            s.step = "Failed"
            s.history.append((s.current_email, "failed"))
            return

        if line.startswith("SKIPPED:"):
            s.skipped += 1
            s.step = "Skipped (failed before Send)"
            s.history.append((s.current_email, "skipped"))
            return

        if line.startswith("RETRY:"):
            parts = [p.strip() for p in line[len("RETRY:"):].split("|", 2)]
            s.retry.append(tuple(parts + [""] * (3 - len(parts))))
            return

        if line.startswith(("Scheduled start:", "Outside send window", "Daily cap reached", "Limit:")):
            s.step = line.rstrip(".")
            return

        if line.startswith("Send window") and "has ended" in line:
            s.stop_requested = True
            s.step = "Send window ended"
            return

        if line.startswith("Skipped before start:"):
            s.precheck = line
            return

        m = re.match(r"^Skipped:\s*(\d+)$", line)
        if m:
            s.skipped = int(m.group(1))
            return

        if line == "Email was not sent.":
            # process_recipient returned False; agent counts it as failed.
            s.failed += 1
            s.last_error = "Email was not sent."
            s.history.append((s.current_email, "failed"))
            return

        if line.startswith("ERROR:"):
            s.last_error = line[len("ERROR:"):].strip()
            s.step = "Error"
            return

        if line.startswith("WARNING:") or line.startswith("Warning:"):
            s.warnings += 1
            return

        if line == "Stopped by user.":
            s.stop_requested = True
            return

        m = re.match(r"^Completed:\s*(\d+)$", line)
        if m:
            s.summary_seen = True
            # The agent's own totals are authoritative.
            s.completed = int(m.group(1))
            return

        m = re.match(r"^Failed:\s*(\d+)$", line)
        if m:
            s.summary_seen = True
            s.failed = int(m.group(1))
            return

        self._advance(line)

    def handle_event(self, ev: dict) -> None:
        """Structured '@@EVENT {json}' lines from the agent (preferred source)."""
        s = self.state
        kind = ev.get("event")
        if kind == "run_start":
            s.dry_run = bool(ev.get("dry_run", s.dry_run))
        elif kind == "eligible":
            s.eligible = int(ev.get("count") or 0)
        elif kind == "no_eligible":
            s.no_eligible = True
            s.step = "No eligible recipients in the CSV"
        elif kind == "login_wait":
            if s.phase in (STARTING, WAITING_LOGIN):
                s.phase = WAITING_LOGIN
                s.step = "Waiting for Crisp login"
        elif kind == "logged_in":
            if s.phase != STOPPING:
                s.phase = RUNNING
            s.step = "Signed in to Crisp"
        elif kind == "recipient":
            s.position = int(ev.get("position") or 0)
            s.eligible = int(ev.get("total") or s.eligible)
            s.current_email = str(ev.get("email") or "")
            s.current_name = str(ev.get("name") or "")
            s.step = "Starting recipient"
            s.step_fraction = 0.0
            if s.phase != STOPPING:
                s.phase = RUNNING
        elif kind == "sent":
            s.sent_confirmed += 1
            self._advance("Email sent successfully")
        elif kind == "success":
            s.completed += 1
            s.step_fraction = 1.0
            s.step = "Dry run passed" if s.dry_run else "Completed"
            s.history.append((s.current_email, "dry-run" if s.dry_run else "sent"))
        elif kind == "skipped":
            s.skipped += 1
            s.step = "Skipped (failed before Send)"
            s.history.append((s.current_email, "skipped"))
        elif kind == "failed":
            s.failed += 1
            s.last_error = str(ev.get("error") or "Failed")
            if ev.get("unconfirmed"):
                s.unconfirmed_send = True
            s.step = "Failed"
            s.history.append((s.current_email, "failed"))
        elif kind == "retry":
            s.retry.append((str(ev.get("email") or ""), str(ev.get("name") or ""), str(ev.get("error") or "")))
        elif kind == "summary":
            s.summary_seen = True
            s.completed = int(ev.get("completed") or 0)
            s.failed = int(ev.get("failed") or 0)
            s.skipped = int(ev.get("skipped") or 0)
        elif kind == "stop_requested":
            s.stop_requested = True
            s.step = "Stopped before the next recipient"
        elif kind == "window_ended":
            s.stop_requested = True
            s.step = "Send window ended"
        elif kind == "error":
            s.last_error = str(ev.get("message") or "Error")
            s.step = "Error"
        elif kind == "health":
            s.health.append((str(ev.get("check")), bool(ev.get("ok")), str(ev.get("detail") or "")))
        elif kind == "health_wait":
            s.step = "Open any conversation in the Crisp window"

    def _advance(self, line: str) -> None:
        s = self.state
        for needle, fraction, label in _STEPS:
            if needle in line:
                if fraction >= s.step_fraction:
                    s.step_fraction = fraction
                s.step = label
                return

    # ------------------------------------------------------------ lifecycle
    def request_stop(self) -> None:
        self.state.stop_requested = True
        if self.state.active:
            self.state.phase = STOPPING

    def finish(self, exit_code: int | None, killed: bool = False) -> RunState:
        """Decide the final phase from what the agent actually reported."""
        self.flush()
        s = self.state
        s.exit_code = exit_code
        if s.failed or s.last_error or (exit_code not in (0, None) and not s.stop_requested and not killed):
            s.phase = FAILED
            if not s.last_error and exit_code not in (0, None):
                s.last_error = f"Agent exited with code {exit_code}"
        elif killed or (s.stop_requested and not (s.summary_seen and s.processed >= s.eligible > 0)):
            # A stop requested during the last recipient still ends the whole
            # list, so that run counts as finished.
            s.phase = STOPPED
        else:
            s.phase = FINISHED
        return s

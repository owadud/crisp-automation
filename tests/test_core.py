"""Core tests: log parsing, CSV stats, agent consistency. Run: python -m pytest tests"""

import csv
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.csv_stats import SENT_VALUES, read_stats  # noqa: E402

# Real output captured from the user's successful live run (dry run OFF).
REAL_SUCCESS_LOG = """\
============================================================
Crisp Email Agent
============================================================
CSV: /home/x/recipients.csv
Shortcut: recommend-offer
Subject: [Action Required] Exclusive VIP Access👀
Reference filter: Shahriar
Segment: pending-cart
Dry run: False

Eligible recipients: 2

Launching Chromium. Complete your Crisp login when the browser opens.
Waiting for Crisp Inbox login...
Crisp Inbox detected. Starting automatically.

Browser is open. Waiting for automatic Inbox detection.
Complete Crisp login in Chromium if requested.

============================================================
[1/2] a@example.com | Al Shahriar
============================================================
  Recipient: a@example.com | Al Shahriar
  Email suggestion selected.
  Subject menu rows (attempt 1): ['[Action Required] Exclusive VIP Access👀']
  Subject chosen from dropdown. Input value: '[Action Required] Exclusive VIP Access👀'
  Create Conversation clicked.
  New-conversation dialog closed; conversation created.
  Opening shortcut picker: recommend-offer
  Crisp Shortcuts tab opened.
  Typing shortcut...
  Selecting shortcut...
  Shortcut inserted successfully.
  Finding Send button...
  Send button found at x=1186, y=848, w=79, h=32
  Clicking Send...
  Email sent successfully.
  Mail Sent marked TRUE.
  Adding segment: pending-cart
  Segment 'pending-cart' added successfully.
  Resolving conversation...
  Conversation resolved.
  SUCCESS.

============================================================
[2/2] b@example.com | WPXPO User
============================================================
  Recipient: b@example.com | WPXPO User
  Clicking Send...
  FAILED: RuntimeError: Send button was clicked, but Crisp did not confirm the send. The email MAY already have been sent - check the conversation in Crisp before running again, to avoid duplicates.
  No Mail Sent mark was added.
  Agent stopped rather than risk sending to the wrong person.

============================================================
Completed: 1
Failed:    1
CSV:       /home/x/recipients.csv
============================================================
"""


def parse(text, chunk=None, dry=True):
    p = rs.RunParser(dry_run=dry)
    data = text.encode("utf-8")
    if chunk:
        for i in range(0, len(data), chunk):
            p.feed(data[i:i + chunk])
    else:
        p.feed(data)
    return p


def test_real_log_counts():
    p = parse(REAL_SUCCESS_LOG)
    s = p.finish(0)
    assert s.dry_run is False
    assert s.eligible == 2
    assert s.sent_confirmed == 1
    assert s.completed == 1
    assert s.failed == 1
    assert s.unconfirmed_send is True
    assert s.phase == rs.FAILED          # exit code 0 must NOT read as success
    assert "MAY already have been sent" in s.last_error
    assert s.progress == 1.0


@pytest.mark.parametrize("chunk", [1, 2, 3, 7, 64])
def test_chunked_output_is_identical(chunk):
    """Lines and multi-byte emoji split across reads must parse the same."""
    a = parse(REAL_SUCCESS_LOG).finish(0)
    b = parse(REAL_SUCCESS_LOG, chunk=chunk).finish(0)
    assert (a.sent_confirmed, a.completed, a.failed, a.eligible) == (
        b.sent_confirmed, b.completed, b.failed, b.eligible)


def test_emoji_survives_byte_split():
    p = rs.RunParser()
    raw = "Subject: VIP👀\n".encode("utf-8")
    lines = []
    for byte in raw:
        lines += p.feed(bytes([byte]))
    assert lines == ["Subject: VIP👀"]


def test_dry_run_success_is_not_sent():
    log = """Dry run: True
Eligible recipients: 1
Crisp Inbox detected. Starting automatically.
[1/1] a@example.com | A
  Recipient: a@example.com | A
  Shortcut inserted successfully.
  [DRY RUN] Send skipped.
  [DRY RUN] Segment and resolve skipped.
  SUCCESS.
Completed: 1
Failed:    0
"""
    s = parse(log).finish(0)
    assert s.dry_run is True
    assert s.sent_confirmed == 0
    assert s.completed == 1
    assert s.phase == rs.FINISHED
    assert s.history == [("a@example.com", "dry-run")]


def test_progress_tracks_steps():
    p = rs.RunParser(dry_run=False)
    p.feed(b"Eligible recipients: 4\nCrisp Inbox detected.\n[1/4] a@x.com | A\n")
    assert p.state.progress == 0.0
    p.feed(b"  Shortcut inserted successfully.\n")
    assert 0 < p.state.progress < 0.25
    p.feed(b"  SUCCESS.\n[2/4] b@x.com | B\n")
    assert p.state.progress == pytest.approx(0.25)
    assert p.state.current_email == "b@x.com"
    assert p.state.position == 2


def test_step_fraction_never_goes_backwards():
    p = rs.RunParser()
    p.feed(b"Eligible recipients: 1\n[1/1] a@x.com | A\n  Shortcut inserted successfully.\n")
    before = p.state.step_fraction
    p.feed(b"  Opening shortcut picker: x\n")   # retry message, earlier step
    assert p.state.step_fraction == before


def test_error_line_and_exit_code():
    s = parse("ERROR: FileNotFoundError: CSV file not found:\n/x.csv\n").finish(1)
    assert s.phase == rs.FAILED
    assert s.last_error.startswith("FileNotFoundError")


def test_nonzero_exit_without_output_is_failure():
    s = parse("").finish(1)
    assert s.phase == rs.FAILED
    assert "code 1" in s.last_error


def test_no_eligible_is_finished():
    s = parse("Eligible recipients: 0\n\nNo eligible recipients.\n").finish(0)
    assert s.phase == rs.FINISHED
    assert s.no_eligible


def test_graceful_stop():
    p = parse("Eligible recipients: 3\n[1/3] a@x.com | A\n  SUCCESS.\n")
    p.request_stop()
    assert p.state.phase == rs.STOPPING
    p.feed(b"\nStop requested. Stopping before the next recipient.\nCompleted: 1\nFailed:    0\n")
    s = p.finish(0)
    assert s.phase == rs.STOPPED
    assert s.completed == 1


def test_force_kill_is_stopped_not_failed():
    p = parse("Eligible recipients: 3\nWaiting for Crisp Inbox login...\n")
    assert p.state.phase == rs.WAITING_LOGIN
    p.request_stop()
    s = p.finish(-1, killed=True)
    assert s.phase == rs.STOPPED


# ------------------------------------------------------------------ CSV
def write_csv(path, rows, header=("Full Name", "Contact Email", "Reference", "Mail Sent")):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_csv_stats(tmp_path):
    p = tmp_path / "r.csv"
    write_csv(p, [
        ("A", "a@x.com", "Shahriar", "TRUE"),
        ("B", "b@x.com", "Shahriar", ""),
        ("C", "", "Shahriar", ""),
        ("D", "d@x.com", "Other", ""),
        ("E", "e@x.com", "shahriar", "no"),
    ])
    s = read_stats(str(p), "Shahriar")
    assert s.ok
    assert (s.total, s.eligible, s.marked_sent, s.missing_email, s.other_reference) == (5, 2, 1, 1, 1)


def test_csv_errors(tmp_path):
    assert not read_stats("", "x").ok
    assert read_stats(str(tmp_path / "nope.csv"), "x").error == "File not found."
    p = tmp_path / "bad.csv"
    write_csv(p, [("a", "b")], header=("Full Name", "Phone"))      # no email column at all
    s = read_stats(str(p), "x")
    assert not s.ok and "No email column" in s.error


@pytest.fixture
def agent(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    csv_path = tmp_path / "agent.csv"
    monkeypatch.setenv("CRISP_CSV_FILE", str(csv_path))
    monkeypatch.setenv("CRISP_REFERENCE_FILTER", "Shahriar")
    sys.modules.pop("crisp_email_agent", None)
    mod = importlib.import_module("crisp_email_agent")
    return mod, csv_path


def test_eligibility_matches_agent(agent, tmp_path):
    """The dashboard's Eligible number must equal what the agent will process."""
    mod, csv_path = agent
    rows = [
        ("A", "a@x.com", "Shahriar", v) for v in sorted(SENT_VALUES | {"", "no", "false", "0", " TRUE "})
    ] + [("C", "", "Shahriar", ""), ("D", "d@x.com", "other", ""), ("E", "e@x.com", " SHAHRIAR ", "")]
    write_csv(csv_path, rows)
    agent_rows, _ = mod.load_csv()
    assert read_stats(str(csv_path), "Shahriar").eligible == len(mod.get_eligible(agent_rows))
    for v in SENT_VALUES:
        assert mod.is_checked(v)

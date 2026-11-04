"""Stand-in for crisp_email_agent.py used by the GUI tests.

It reads the same environment variables, applies the same CSV rules, and
prints the exact lines the real agent prints, but never opens a browser.
Behaviour knobs (test only):
  FAKE_ENV_OUT     write the received CRISP_* environment as JSON here
  FAKE_FAIL_EMAIL  this recipient fails with the real "MAY already have been sent" error
  FAKE_STEP_DELAY  seconds between steps (default 0.02)
  FAKE_LOGIN_WAIT  seconds to sit in "waiting for login"
"""

import csv
import json
import os
import sys
import time
from pathlib import Path

env = {k: v for k, v in os.environ.items() if k.startswith("CRISP_")}
if os.environ.get("FAKE_ENV_OUT"):
    Path(os.environ["FAKE_ENV_OUT"]).write_text(json.dumps(env), encoding="utf-8")

CSV_FILE = Path(env["CRISP_CSV_FILE"])
DRY = env.get("CRISP_DRY_RUN", "true").lower() in {"1", "true", "yes", "on"}
REF = env.get("CRISP_REFERENCE_FILTER", "")
STOP = env.get("CRISP_STOP_FILE", "")
DELAY = float(os.environ.get("FAKE_STEP_DELAY", "0.02"))
FAIL = os.environ.get("FAKE_FAIL_EMAIL", "")
SENT = {"true", "1", "yes", "y", "checked", "sent", "x", "✓", "☑"}


def out(text=""):
    print(text, flush=True)
    time.sleep(DELAY)


def main():
    out("=" * 60)
    out("Crisp Email Agent")
    out("=" * 60)
    out(f"CSV: {CSV_FILE}")
    out(f"Subject: {env.get('CRISP_SUBJECT')}")
    out(f"Dry run: {DRY}")
    out()
    with CSV_FILE.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames)
        rows = list(reader)
    eligible = [
        (i, r) for i, r in enumerate(rows)
        if r["Reference"].strip().lower() == REF.lower()
        and r["Contact Email"].strip()
        and r["Mail Sent"].strip().lower() not in SENT
    ]
    out(f"Eligible recipients: {len(eligible)}")
    out()
    if not eligible:
        out("No eligible recipients.")
        return
    out("Launching Chromium. Complete your Crisp login when the browser opens.")
    out("Waiting for Crisp Inbox login...")
    wait = float(os.environ.get("FAKE_LOGIN_WAIT", "0"))
    end = time.time() + wait
    while time.time() < end:
        out("Still waiting for Crisp login / Inbox to finish loading...")
        time.sleep(0.2)
    out("Crisp Inbox detected. Starting automatically.")
    completed = failed = 0
    for pos, (idx, row) in enumerate(eligible, 1):
        email, name = row["Contact Email"].strip(), row["Full Name"].strip()
        if STOP and Path(STOP).exists():
            out()
            out("Stop requested. Stopping before the next recipient.")
            break
        out()
        out("=" * 60)
        out(f"[{pos}/{len(eligible)}] {email} | {name}")
        out("=" * 60)
        out(f"  Recipient: {email} | {name}")
        out("  Email suggestion selected.")
        out(f"  Subject chosen from dropdown. Input value: {env.get('CRISP_SUBJECT')!r}")
        out("  Create Conversation clicked.")
        out("  New-conversation dialog closed; conversation created.")
        out("  Opening shortcut picker: x")
        out("  Shortcut inserted successfully.")
        if DRY:
            out("  [DRY RUN] Send skipped.")
        else:
            out("  Finding Send button...")
            out("  Clicking Send...")
            if email == FAIL:
                failed += 1
                out("  FAILED: RuntimeError: Send button was clicked, but Crisp did not confirm the send. "
                    "The email MAY already have been sent - check the conversation in Crisp before running "
                    "again, to avoid duplicates.")
                out("  No Mail Sent mark was added.")
                out("  Agent stopped rather than risk sending to the wrong person.")
                break
            out("  Email sent successfully.")
            rows[idx]["Mail Sent"] = "TRUE"
            with CSV_FILE.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields)
                w.writeheader()
                w.writerows(rows)
            out("  Mail Sent marked TRUE.")
        if DRY:
            out("  [DRY RUN] Segment and resolve skipped.")
        else:
            out("  Adding segment: x")
            out("  Resolving conversation...")
        completed += 1
        out("  SUCCESS.")
    out()
    out("=" * 60)
    out(f"Completed: {completed}")
    out(f"Failed:    {failed}")
    out("=" * 60)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # mirror the real agent
        print(f"\nERROR: {type(exc).__name__}: {exc}", flush=True)
        sys.exit(1)

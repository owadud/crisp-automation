# FEATURES.md: full feature list by phase
Duplicates from the original list are merged. Item numbers match the original list. Read only the section for the current phase.

## Phase 1: GitHub one-click update
- 43 One-click install and update. Teammates click "Update" in the app instead of downloading a folder. Replaces update_ubuntu.sh / update_windows.bat (keep them as fallback).
- 53 Update check. On app start (non-blocking) and via a "Check for updates" button, read the latest GitHub Release (tag, zip URL, sha256). Show "v3.1 available" only if the tag is numerically newer.
- Update flow: download zip to temp, verify sha256, back up current code to backups/<old_version>/, replace ONLY code files, restart. Never touch settings, CSVs, history, browser profile, logs, key files. Auto-rollback on failure with a clear message.
- VERSION file as the single source of truth (replace APP_VERSION constant usage).
- Release helper: script or GitHub Action on tag push that builds the zip without dev/debug files and writes the sha256.
- 34 Repo hygiene: .gitignore exists, debug and backup files are in dev/.
- 12 recipients_template.csv header only, default CSV path empty (already done in this package).
- Guide "How to publish a new version", max 5 steps.
- Tests: version compare, checksum failure, rollback.

## Phase 2: Row range selection
- "From row" and "To row" fields, blank = all. Serial = position among data rows (header excluded, starts at 1), fixed BEFORE duplicate or already-sent filtering.
- Live preview: "Rows 200-300: 101 rows, 85 eligible, 3 duplicates, 4 already sent".
- Validate from <= to and within file size. Error message instead of a crash.
- Remember last range, reset when a different file is loaded. Show a Serial column if a table view exists.

## Phase 3: Safety
- 1 Remove duplicate emails (trim, lowercase, skip repeats, show "3 duplicates skipped").
- 2 Validate email format before the run (catches a@b and gmial.com). Flag bad rows, do not stop the run.
- 3 CSV backup before each run (recipients.YYYY-MM-DD-HHMM.bak.csv).
- 4 Check Crisp for an existing conversation with that email before creating one.
- 5 Crash recovery on startup: "Last run stopped at a@x.com. Check Crisp before continuing."
- 6 Do-not-contact list: a second CSV of emails never sent to.
- 7 Auto-stop after N failures in a row.
- 8 Lock the CSV during a run with a .lock file so two copies cannot double-send.
- 9 Skip failed recipient and continue: opt-in, max N, default stays "stop".
- 10 Skip-and-continue only for failures BEFORE the Send click (bad email, subject not found). Always stop at or after Send and give a retry list at the end.

## Phase 4: Sending control and records
- 14 Max N emails per run and a daily cap, as settings.
- 15 Configurable delay between emails (currently fixed 3 s).
- 16 Optional random delay 20-60 s.
- 17 Send window, e.g. only 10:00-18:00 Dhaka time.
- 18 Scheduled start ("start at 10:00 AM Dhaka"). Only works if the computer is on and the Crisp login is valid.
- 22 More CSV columns: Sent At, Status (Sent/Failed/Skipped/Invalid/Unconfirmed), Error, Conversation link.
- 23 "Retry failed only" button.
- 24 Persistent history.csv (time, email, result, subject, dry/live).
- 25 Dashboard "sent in the last 7 days" from history.
- 26 One folder per run: run.log, recipients_before.csv, recipients_after.csv, settings.json, failure screenshots.
- 27 History page in the GUI with "Open folder"; auto-delete runs older than about 30 days.
- 28 Run-end report export as CSV (who succeeded, who failed, why).
- 29 "Copy diagnostics" button: one zip with masked log, versions, settings, self-test results. No Crisp login.

## Phase 5: Stability
- 31 Agent prints structured "@@EVENT {json}" lines so the GUI does not depend on log sentences.
- 32 GUI reads events first, falls back to text matching if none arrive.
- 33 Test that runs the real main() with the browser stubbed and asserts the events.
- 11 "Check Crisp" health button: finds subject dropdown, Shortcuts tab and segment field without sending.

## Phase 6: Google Sheet as the source
Defaults: columns Full Name, Contact Email, Reference, Mail Sent. CSV stays as fallback.
- 57 Paste a Google Sheet link instead of choosing a CSV.
- 58 Auto-tick Mail Sent after each send that Crisp confirms.
- 59 Also write Sent At, Status, Error, conversation link back into the row.
- 60 Find the row by email, never by row number.
- 61 Record each send locally first, then sync to the Sheet, so a dropped connection cannot cause a re-send.
- 62 Lock inside the Sheet: hidden control tab "Running: <name> since 10:02", refreshed every 30 s, stale after a few minutes.
- 63 One-time Google setup: Cloud project, Sheets API, service account, Sheet shared as Editor. Give max 5 lines of steps.
- 64 Key file kept outside the project folder and outside any zip.
- 65 Real checkbox column for Mail Sent, private Sheet (not "anyone with the link").
- 66 GUI: source switch (CSV or Sheet), tab dropdown, "Test connection" ("120 rows, 45 eligible, 3 duplicates").
- 67 Pace Google writes to stay under rate limits.

## Phase 7: Convenience (only if asked)
- 13 Preview of first recipient's subject, shortcut and segment, plus "send one to my own email" test-send.
- 19 Named campaign presets (VIP Access, Pick Up Where You Left Off, Black Friday) that also catch wrong-shortcut mistakes.
- 38 Flexible CSV import: choose which column is email, name, reference.
- 39 Excel (.xlsx) import.
- 40 WooCommerce abandoned-cart export import (needs a sample file first).
- 41 Notification when a run ends or fails: desktop toast, optionally Telegram, WhatsApp or Slack.
- 42 Multiple Crisp accounts, each with its own browser profile and a dropdown.
- 44 Check Crisp's official API docs: only switch if it can do the exact flow.

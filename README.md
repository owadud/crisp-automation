# Crisp Automation Desktop

Cross-platform desktop GUI for the existing Crisp email automation workflow.

## What's new in 3.0

**Redesigned interface.** Light and dark themes (plus System), sidebar navigation
(Ctrl+1 to Ctrl+4), and four pages:

- **Dashboard.** Recipients in CSV, Eligible to send, Sent, and Failed. *Sent* only
  counts emails Crisp confirmed (the agent's `Email sent successfully.` line). A dry
  run never counts as sent. Also shows the current run and a summary of the last run.
- **Campaign Setup.** CSV with live validation (rows, eligible, already sent), subject,
  shortcut, reference filter, segment, and the Dry Run switch. Fields lock while a run
  is in progress.
- **Activity Logs.** Live colour-coded log, real progress (recipient X of N plus the
  current step), Sent/Failed/Remaining counts, and Stop, Copy, Save and Clear.
- **Settings.** Theme, browser session folder (Open / Copy path), reset campaign
  fields, and version info.

**Safety and correctness fixes:**

- A run that hits `FAILED:` is now reported as **Failed**. Before, it showed "Run
  finished" because the agent exits with code 0.
- An agent startup error (e.g. CSV missing) no longer hangs the app forever on
  "Press ENTER".
- Output is buffered by full lines, so status lines and emoji split across reads are
  parsed correctly.
- **Stop waits for the current recipient to finish**, then stops before the next one,
  so it never cuts off a send halfway. *Force stop* is still available. During login
  (nothing being sent yet), Stop is immediate.
- Real sending needs a confirmation that shows the recipient count, subject,
  shortcut, segment and CSV. Every launch starts with Dry Run ON.
- If Crisp doesn't confirm a send, a warning names the recipient so you can check
  Crisp before re-running (prevents duplicates).
- Campaign fields, theme and window size are saved between launches. The browser
  profile (your Crisp login) is unchanged: `~/.crisp_email_agent/browser_profile`.

**Subject fix (3.0.1):** the agent sometimes created conversations with a cut-off
subject (e.g. "⏳ Final Ho"). When the subject wasn't listed straight away, it typed
only the first 10 characters to filter Crisp's dropdown, then accepted any row that was
*part of* the subject. It now types the full subject, accepts only an exact match, and
checks the Subject field holds the complete text before clicking Create Conversation.
If it can't, the run stops with an error rather than sending with the wrong subject.

**Agent changes (minimal, no workflow changes):** `crisp_email_agent.py` only got
(1) a check between recipients for the GUI's stop file (`CRISP_STOP_FILE`) and
(2) exit code 1 instead of `input()` on errors when run from the GUI. Every Crisp
step (subject, shortcut, send, verification, segment, resolve) is unchanged.

**Code layout:** `gui_app.py` (entry point, launched by the same run scripts) plus the
`crisp_gui/` package (`theme`, `icons`, `widgets`, `pages`, `main_window`,
`agent_process`, `run_state`, `csv_stats`, `config`).

**Tests:** `pip install pytest`, then `python -m pytest tests` (on Linux, use
`xvfb-run -a python -m pytest tests` to include the real-Chromium stop test).
`tests/fake_agent.py` prints the real agent's exact lines, so full runs can be tested
without Crisp.

## Ubuntu 24.04 / 26.04

1. Extract this folder.
2. Open Terminal in `Crisp_Email_Agent`.
3. Run the one-time setup (no execute permission/chmod is required):

   ```bash
   bash setup_ubuntu.sh
   ```

4. Launch the application:

   ```bash
   bash run_ubuntu.sh
   ```

Run setup only once. Later launches only need `bash run_ubuntu.sh`.

## Windows 10 / 11

1. Extract this folder.
2. Double-click `run_windows.bat`. On the first launch it runs setup automatically
   (installs Python 3.12 with winget if needed, the Python packages, and Chromium),
   then opens the app. Later launches start straight away.

If Windows shows "The publisher could not be verified" or SmartScreen, that's because
the script is unsigned and came from a download. Click **Run** (or **More info, Run
anyway**). To stop the prompt appearing, right-click the zip, choose **Properties**,
tick **Unblock**, click OK, then extract it again.

If winget is unavailable, install Python 3.12+ from https://www.python.org/downloads/windows/ and enable **Add Python to PATH**, then run setup again.

## First run and safety

- Select the real recipient CSV in the GUI. `recipients_template.csv` is only a header template.
- The browser opens separately. Sign in to Crisp manually when prompted.
- Keep Dry Run enabled for the first test. Confirm the correct recipient, subject, shortcut content, and segment before disabling Dry Run.
- Never run two copies against the same CSV at the same time.
- The persistent browser profile is stored in the user's home directory, not in this project folder.
- Do not commit recipient CSVs, browser profiles, credentials, or session data to GitHub.

## Safety (3.2)

- **Skipped before the run starts** (shown in the CSV preview and the log): duplicate
  emails (trimmed, lower-case; an email already marked sent, or repeated in this run),
  invalid addresses (e.g. `a@b`, `gmial.com`, `.con`) and anything on the optional
  **do-not-contact** list. Bad rows are flagged; the run does not stop because of them.
- **CSV backup** before each real run: `recipients.YYYY-MM-DD-HHMM.bak.csv` next to the CSV.
- **Lock file** `recipients.csv.lock` while a run is active, so two copies cannot send
  from the same CSV. A lock left by a crashed run on the same PC is replaced automatically.
- **Skip and continue** (opt-in, off at every launch): only failures *before* the Send
  click are skipped, up to *Max skipped per run*. A failure at or after Send always stops.
  The run auto-stops after *N failures in a row*. A retry list is printed at the end.
- **Crash recovery:** if the app closed during a run, the next start says which
  recipient it stopped at so you can check Crisp first.
- Not done yet: checking Crisp for an existing conversation before creating one. It
  needs a live Crisp page sample to build safely.

## Sending control and records (3.3)

Campaign Setup > **Sending control** (all times are Dhaka time, UTC+6):

- **Delay between emails** (default 3 s) or a **random 20–60 s** delay.
- **Max emails per run** and a **daily cap** (real sends per Dhaka day, counted from `history.csv`).
- **Send window**, e.g. `10:00-18:00`: waits if it is too early, stops cleanly when the window ends.
- **Start at**, e.g. `10:00`: the run waits, then opens Crisp at that time. Only works if
  this computer is on and awake and the Crisp login is still valid.
- **Retry failed only**: re-runs rows whose Status is Failed or Skipped. Unconfirmed rows
  are never retried automatically (that email may already have gone out).

Records:

- Real runs add CSV columns **Sent At, Status** (Sent/Failed/Skipped/Invalid/Unconfirmed),
  **Error** and **Conversation link**.
- `history.csv` gets one line per recipient (time, email, result, subject, dry/live). The
  Dashboard shows real sends in the last 7 days.
- One folder per run in `runs/`: `run.log`, `settings.json`, `recipients_before.csv`,
  `recipients_after.csv`, `report.csv`, `summary.json` and failure screenshots. Runs older
  than 30 days are deleted automatically.
- **History** page: list of runs, Open folder, Export report, and **Copy diagnostics** (a zip
  with the log masked, versions, settings and a self-test; never the Crisp login or CSVs).

## Stability (3.4)

- The agent prints structured lines `@@EVENT {"event": ...}` next to its normal log
  (run_start, eligible, logged_in, recipient, sent, success, skipped, failed, retry,
  summary, stop_requested, window_ended, error, health). The app reads these first, so
  changing a log sentence can't break the counts. If no events arrive (an older agent),
  it falls back to reading the text. Event lines are kept in `run.log` and hidden on screen.
- **Settings > Check Crisp**: opens Crisp and checks the subject dropdown (New
  conversation dialog, closed again), the Shortcuts tab and the segment field (open any
  existing conversation when asked). Nothing is created or sent.

## Google Sheet as the source (3.5)

Campaign Setup > Recipients > **Source: Google Sheet**. Same columns as the CSV (Full
Name, Contact Email, Reference, Mail Sent); the CSV stays available as before.

**One-time Google setup (5 steps):**
1. console.cloud.google.com: create a project, then enable the **Google Sheets API**.
2. IAM & Admin > Service accounts > Create; open it > Keys > Add key > JSON (downloads a key file).
3. Store the key file **outside** the app folder (e.g. Documents\crisp-key.json). The app refuses a key inside it.
4. In the Sheet: Share > add the service account's email (`...@...iam.gserviceaccount.com`) as **Editor**.
   Keep the Sheet private (not "anyone with the link"). Select the Mail Sent column > Insert > **Checkbox**.
5. In the app: paste the Sheet link, choose the key, **Load tabs**, pick the tab, **Test connection**.

How it runs: the tab is downloaded into a local working copy; every result is saved locally
first and then written to the Sheet row found by **email** (Mail Sent ticked, Sent At, Status,
Error, Conversation link). If the internet drops, results wait in a local queue and are written
at the next start; those emails are never sent again. Writes are paced (about 1 per second).
While a real run is active, a hidden `_crisp_control` tab shows "Running: <PC> since 10:02",
refreshed every 30 s; another PC can start only when it is older than 5 minutes.
Dry runs never write to the Sheet.

## Email-only files, history rules, pause and ETA (3.6)

**Only an email column is required** (CSV or Google Sheet). The header can be `Contact Email`,
`Email`, `email` or `E-mail` (any case, extra spaces ignored). All your other columns are kept
unchanged.
- No **Mail Sent** column: it is added on the first real run, FALSE on every row.
- No/empty **Full Name**: Crisp gets a name made from the email (`john.doe@x.com` -> `John Doe`),
  never a blank one. The preview marks these "(from email)".
- No/empty **Reference**: the row is sent, and afterwards your Reference (e.g. Shahriar) is
  written into it. Rows with someone else's Reference are still skipped.
- The summary says what it found, e.g. `Found 120 rows · 118 valid emails · 2 invalid ·
  3 duplicates skipped · 5 skipped: emailed in the last 7 days · 110 to send ·
  optional columns missing: Full Name, Reference`.

**History rules** (`history.csv`, all campaigns and all files, real sends only):
- **Campaign name** (blank = the subject): one person is never emailed twice in the same campaign.
- **Skip anyone emailed in the last N days** (default 7, 0 = off), across all campaigns.
  "Unconfirmed" sends count as possibly sent and are skipped with a warning.
- **Preview rows…** shows every row: email, the name that will be used, subject, shortcut,
  segment, reference and the action (Send / Skip: duplicate / invalid / emailed recently /
  already sent ...), with who and when for recent contacts. The same table is shown before every
  real run as the confirmation.
- A **dry run never writes** history, the CSV/Sheet or Mail Sent.

**Pause / Resume** (Activity Logs): takes effect between two recipients, never during a send.
Shows "Paused after a@x.com"; Resume continues with the next recipient (nobody is resent).
Closing the app while paused counts as a stopped run.

**ETA**: "12 of 50 done · ETA 14:32 (about 18 min)", based on the real pace including the delay
between emails (fixed or random) and ignoring paused time.

## Shortcut behavior

The automation clicks the visible **Shortcuts** tab in Crisp. It deliberately does not use Ctrl+K because that opens Crisp's global command palette in the current interface. If the Shortcuts tab cannot be found, the run stops with a clear error rather than opening the wrong panel.

## Updates (one click)

**Settings > Updates > Check for updates**, then **Update now**. The app also checks
quietly at start and shows "vX.Y available" in the sidebar. It downloads the latest
GitHub Release, verifies its sha256, backs up the current code to `backups/<old version>/`,
replaces only code files, and restarts. Settings, CSVs, history, logs, keys, `runs/`,
`.venv/` and the Crisp login are never touched. If anything fails, the old code is
restored automatically. The version number lives only in the `VERSION` file.

The GitHub repository must be **public** for the updater to read releases without a login.

Fallback (needs git and a clone): `bash update_ubuntu.sh` or `update_windows.bat`.

### How to publish a new version

1. Change the number in `VERSION` (for example `3.1`) and commit + push.
2. Tag it: `git tag v3.1` then `git push origin v3.1` (the tag must match `VERSION`).
3. Wait about 1 minute: GitHub Actions builds the zip and `.sha256` and publishes the Release.
4. Check the repo's **Releases** page shows `crisp-automation-v3.1.zip` and its `.sha256`.
5. Teammates click **Check for updates > Update now**. (To test the zip locally: `python tools/make_release.py`.)

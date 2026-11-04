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

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
Dry runs never write to the Sheet.

### Several people on one Sheet at the same time (3.8)

Teammates can send from the **same Sheet at the same time**, each on their own computer,
in the same or different campaigns. There is no "Sheet is in use" lock any more.
- Each recipient is **claimed by email** (never by row number) in a hidden `_crisp_claims` tab,
  a small batch at a time (Settings > Shared Google Sheet > Batch size, default 10).
- Right before every send, the app re-reads Mail Sent and its claim. If someone else has it, or
  it was ticked meanwhile, it is skipped and logged ("Not sent by this runner").
- "Sending" is written just before the Send click. A recipient left in **Sending** or
  **Unconfirmed** (a computer crashed after clicking Send) is never retried automatically:
  check it in Crisp.
- Each runner writes a heartbeat to `_crisp_runners` every 30 s. If a runner stops responding
  for the claim expiry (default 5 minutes), its **unsent** claims are released for others,
  with the reason written in the Note column.
- Sends by every runner count for "already sent in this campaign" and "emailed in the last
  N days". Two runners never email one person on the same day, even in different campaigns.
- Stop releases only this runner's claims that never reached Sending. Pause keeps them.
- Do not edit or sort the `_crisp_claims` tab by hand: the order of its rows decides who
  claimed first.
- Every computer that sends from a shared Sheet needs version 3.8 or later. An older
  version is refused while a 3.8 runner is active, and an older version's run blocks 3.8.

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

## Campaign Results (3.7)

**Campaign Results** page: what happened after sending, read from Crisp with your saved
login. **Read-only**: it only opens each recorded conversation link and reads it; nothing
is typed, sent, resolved or changed.

- **Refresh statuses** for any campaign (or all). Pages open one every few seconds with a
  progress bar; Stop works between pages. The check stops by itself if the Crisp login is
  lost or the page layout is not recognised.
- Statuses: **Sent, Delivered, Read, Replied, Failed / Not delivered, Unconfirmed**. The
  highest status is kept and never downgraded by a later check.
- **Read is approximate** (email open tracking: mail apps can auto-open or block images).
  **Replied is reliable** (a customer message after ours).
- Times: Crisp shows the exact **sent** and **reply** times. It shows no time for delivered
  or read, so those are the time we first noticed them, labelled **"first seen"**.
- Summary cards with delivered / read / reply %, a detail table (filter by status, shortcut
  version and date; sortable; clickable conversation links; **Export CSV**), **Needs
  follow-up** (replied and still open), **Delivery problems** (with the reason if Crisp shows
  one, and an optional, confirmed "Add to do-not-contact"), **Compare** (campaigns side by
  side, and shortcut versions within a campaign) and **Check history** (how numbers grow).
  Re-checks are suggested about 1 hour, 1 day and 3 days after sending.
- **Shortcut version** (Campaign Setup > Message): your label for the shortcut text, e.g. `v2`.
- **A/B test (3.8)**: enter several shortcuts separated by commas, e.g. `!subs-small, !subs-big`,
  and labels in the same order, e.g. `v1, v2` (blank = the shortcut names). Each recipient gets
  the shortcut with the fewest real sends so far in this campaign (this computer's history), so
  the split stays even, also across runs. The preview table and the live confirmation show which
  shortcut each row is expected to get. Compare the versions in Campaign Results > Compare.
  All shortcuts must use the same Email subject.
- **Message editor (3.12)**: in a Crisp API campaign the Message text box has a toolbar: Bold, Italic,
  Strikethrough, lists, Quote, Code, Link, Image, `{first_name}`, `{name}`, `{ai_opener}`, an A/B
  divider and a Preview. It writes the same Markdown Crisp stores for your shortcuts (`**bold**`).
  Send yourself a test email (Settings > Crisp API) to see how it looks in an inbox.
- **AI opening lines (3.12)**: Campaign > Message > *Personalize the opening line with AI (Claude)* >
  **Generate with AI…**. Claude (`claude-opus-5-5`, low effort, with Anthropic's automatic fallback if
  it declines) writes one or two personal sentences per recipient; your approved message stays as it
  is. Claude gets your subject and message and, per person, only a first name and the company from the
  email domain, never the email address. Review and edit every line in **Lists** (double-click); the
  live confirmation shows how many are ready. Sending uses exactly the stored lines; a person without
  one gets the email without it. The line goes into `{ai_opener}`, or after the greeting. Needs your
  own Anthropic API key in Settings > AI (Claude), stored only on this computer; roughly US$0.002 per
  recipient. Needs Python 3.10 or newer (Windows and Ubuntu setups have it).
- **Read at (3.11)**: Crisp's API says that an email was read, not when. With the API token saved,
  the app re-checks emails that have no reply yet (sent in the last 14 days) every 30 minutes while
  it is open, quietly. Results > **Read at** then shows the window it happened in, e.g.
  `2026-11-12 10:00–10:30`. A reply has its exact time. Switch it off in Settings > Crisp API.
  Automatic checks use at most 6,000 of the token's 10,000 requests a day.
- **Scheduled campaigns (3.11)**: Campaigns > select a campaign > **Schedule…** > pick a date and time
  (Dhaka) > confirm. The mode is the top switch at that moment (Dry Run or LIVE), and the
  confirmation names the campaign, time, mode, subject, message and recipient count. At that time
  the campaign starts by itself (only that campaign, with all the usual rules). The app must be
  open and the computer awake: if it could not start within 15 minutes, it is marked missed and
  **nothing is sent late**. **Cancel schedule** removes it.
- **Send with the Crisp API (3.11)**: in a campaign, **Message > Send with: Crisp API** sends without
  a browser: the app creates the conversation in Crisp, sets the email, name, subject and segment,
  posts your **Message text** (`{name}` / `{first_name}` are filled in; a line with only `---`
  separates A/B versions) and resolves it. All the usual rules apply: claims, recently-contacted,
  pace, daily cap, Stop/Pause and the live confirmation. If Crisp refuses the message it is Failed
  (nothing sent); if the connection breaks while posting it is **Unconfirmed** and never repeated.
  Needs the API token (Settings > Crisp API). **Browser** (Crisp shortcut) stays the default.
- **Crisp API (3.10)**: with a Crisp Website Token saved in **Settings > Crisp API**, **Refresh
  statuses** reads every conversation straight from the Crisp REST API (read-only): delivered, read,
  replied (exact time) and open/resolved, with no browser window and no sign-in. About 2 requests per
  conversation; the token allows 10,000 a day. Create it in Crisp (workspace owner): Settings >
  Workspace Settings > Advanced configuration > API Token > Generate Token, then paste the
  identifier and key into the app and click **Save and test**. The token is stored only in
  `~/.crisp_email_agent/crisp_api.json` on this computer. Without a token, Refresh statuses uses the
  browser as before. Sending still uses the browser (the API cannot use saved Shortcuts).
- **Import or sync… (3.8.3)**: Results only knows sends made on THIS computer. To add sends made on
  another computer or with an older version, click **Import or sync…**, choose any Google Sheet tab
  (link + tab + key) or CSV file, the campaign they belong to and, optionally, a Reference (only
  your rows). Rows with Mail Sent ticked come in with their Sent At time and conversation link; the
  list is only read. Tick **Keep in sync** and that list is read again before every **Refresh
  statuses** (or click **Sync now**), so sends from every computer appear. **Synced lists** stops
  syncing one. Importing twice never adds a send twice.
- Data stays on this computer: `history.csv` (now also saves shortcut, version and the
  conversation link of every real send) and `campaign_results.json`. No message text and no
  screenshots are stored. Sends from before 3.7 have no saved link, so they cannot be checked.

## The app at a glance (3.8)

The left side has six places. The main flow is: **Campaigns > open or create one > check the numbers > Dry Run > Send**.
- **Campaigns**: your saved campaigns and the queue. **New campaign** (Ctrl+N), double-click to open,
  **Run queue**. Only the actions that fit the selected campaign are shown.
- **Campaign** (opened from Campaigns): the list, message and targeting, with the numbers on top
  (in list, will be sent, sent, failed). Rarely used settings (row range, safety, pace and timing)
  are under **Advanced options**. **Save campaign**, **Save as new**, **Save and add to queue**.
- **Dry Run / LIVE** is always in the top bar, with an icon and words, on every page. The
  Start button turns red ("Start sending") when Dry Run is off, and the confirmation names the
  exact number of emails, the campaign, the subject and the shortcut(s).
- **Run**: progress, ETA (counting the delay between emails), Pause / Stop, a per-recipient result
  table that stays fast with thousands of rows, and the log.
- **Results**: Campaign Results from Crisp; **Runs and diagnostics** opens the run history.
- **Lists**: every row of the open campaign's list and what a run would do with it.
- **Team**: who is sending from the same Google Sheet, refreshed every 30 s, and recipients that
  need a check in Crisp (record "It was sent" / "It was not sent" after checking).
- **Settings**: theme, your name, updates; everything else under **Advanced**.

## Campaigns and the queue (3.8)

A **campaign** is a saved, named setup: its own list (CSV or Sheet tab), subject, shortcut(s),
segment, reference, row range and safety settings, with its own status and results.
- **Campaigns** page: fill in Campaign Setup, then **Save setup as campaign**. **Open in setup**
  loads a campaign back for editing; **Update from setup** saves the changes. **Duplicate** copies one.
- **Queue**: **Add to queue**, **Move up/down**, **Run next**, **Remove from queue**. **Run queue**
  runs them one after another in your Crisp browser. One confirmation names the campaigns, the
  exact number of emails, people who are in more than one campaign, and the expected time.
- Statuses: Draft, Ready, Queued, Running, Paused, Finished, Stopped, Failed, Unconfirmed.
- **Pause** / **Stop campaign** act on the selected campaign only, between two recipients. A
  paused queued campaign is skipped until resumed. A failure or an unconfirmed send stops the
  queue (stop on failure, as before).
- A person in two campaigns is emailed once: the queue always skips anyone emailed in the last
  day or more (the "emailed recently" rule never drops below 1 day in a queue).
- **The delay between emails and the daily cap are app-wide**: every campaign (and a normal
  run) shares one send turn, so several campaigns never send faster than one.
- Closing the app keeps queued and paused campaigns. A campaign that was sending becomes
  **Unconfirmed**: check its last conversation in Crisp, then click **I checked Crisp**. The queue
  never starts by itself.
- **Settings > Campaign queue > Campaigns at the same time** (default 1). With 2 or 3, each extra
  campaign runs in its own browser window and profile (`browser_profile_2`, `_3`) and needs its
  own Crisp sign-in the first time (each about 300-500 MB of memory). They share your Crisp
  account and the same app-wide pace, so this is not faster; it only lets campaigns progress side
  by side. Two campaigns never share one browser tab: the subject and composer would mix up.

## Open it like an app (no terminal)

**Windows:** double-click `run_windows.bat` once. It finishes the setup if needed, creates a
**Crisp Automation** shortcut on the Desktop and in the Start menu, and opens the app; the black
window closes by itself. From then on, open **Crisp Automation** from the Desktop or Start menu
(right-click it > **Pin to taskbar** to keep it there). No console window opens, also not for
the automation or its browser. `run_windows.bat console` still shows every message, for
troubleshooting.

**macOS (MacBook, Apple silicon or Intel):** once, open Terminal in the app folder (Finder:
right-click the folder > New Terminal at Folder) and run `bash setup_mac.sh`. It installs what the
app needs (Python from Homebrew if missing, or tells you where to get it) and adds **Crisp
Automation** to your Applications folder. From then on open it from Launchpad, Applications or
Spotlight (Cmd+Space, type Crisp); keep it in the Dock with right-click > Options > Keep in Dock.
The first time the app opens Chromium, macOS may ask to allow it. Conversation links open in
Google Chrome if it is installed. `bash run_mac.sh` starts it from Terminal for troubleshooting.

**Ubuntu:** run `bash run_ubuntu.sh` once (or `bash install_launcher_ubuntu.sh`). It adds
**Crisp Automation** to the app grid (Super key, type "Crisp"); right-click its dock icon >
**Pin to Dash**.

Both: **Settings > Open like an app > Create shortcut** does the same from inside the app (use it
again after moving the app folder). Updates keep working, because the shortcut points at this
folder. If the app ever fails to start, an error box shows the reason and it is saved in
`~/.crisp_email_agent/app.log`.

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

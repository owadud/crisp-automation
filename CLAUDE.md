# Crisp Automation Desktop: rules for Claude Code

Existing app: Python + PySide6 GUI (`gui_app.py`, `crisp_gui/`) that runs `crisp_email_agent.py` (Playwright) to send emails through the Crisp web page. CSV columns: Full Name, Contact Email, Reference, Mail Sent.
Do NOT rewrite the app. Make small, targeted changes.

## Token-saving rules
1. Never read the whole project. List files first, open only what the task needs.
2. Never paste whole files in answers. Show only changed functions or diffs.
3. Edit in place. Do not regenerate unchanged code.
4. ONE phase at a time. Plan (max 10 lines) -> implement -> run tests -> summary (max 8 lines: files changed, how to test, manual steps). Then STOP and wait for "next". One commit per phase.
5. Ask at most one question per phase. If unclear, choose the safest default, state it in one line, continue.
6. Do not touch Crisp selectors or browser automation unless the phase requires it.
7. Tests only for new logic, small, with stubs. Existing tests must keep passing.
8. Keep existing behavior. Safe defaults: stop on failure, Dry Run on at every launch.
9. Never commit customer emails, key files, logs, browser profile, debug files.
10. The full feature list is in FEATURES.md. Read ONLY the section for the current phase.

## Facts about the current code
- Version lives in `crisp_gui/config.py` (APP_VERSION). Phase 1 makes a VERSION file the single source of truth.
- Current update method is `update_ubuntu.sh` / `update_windows.bat` = `git pull`. This needs git and a clone on every teammate PC. Phase 1 replaces it with a download-from-GitHub-Release updater (no git needed for teammates). Keep the old scripts as a fallback until the user confirms.
- User data that updates must NEVER overwrite: QSettings preferences, recipient CSVs, history, `~/.crisp_email_agent/browser_profile`, logs, key files, `backups/`, `runs/`.
- Dev/debug files live in `dev/` (git-ignored).
- Default CSV path is intentionally empty. `recipients_template.csv` is header-only.

## Phases (user says "Do Phase N")
1. GitHub one-click update
2. Row range selection (e.g. rows 200-300 of 2000)
3. Safety
4. Sending control and records
5. Stability
6. Google Sheet as the source
7. Convenience (only if asked)

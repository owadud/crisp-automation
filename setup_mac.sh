#!/usr/bin/env bash
# Crisp Automation - macOS first-time setup (Apple silicon and Intel).
# In Terminal, in this folder:   bash setup_mac.sh
# It installs what the app needs into .venv (only here), then adds
# "Crisp Automation" to your Applications folder. Run it once.
set -Eeuo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"

echo '========================================='
echo ' Crisp Automation - macOS first-time setup'
echo '========================================='

# A Python 3.9+ (newest first). Homebrew and python.org installs are preferred
# over Apple's; Apple's /usr/bin/python3 works too.
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 \
            /opt/homebrew/bin/python3 /usr/local/bin/python3 \
            /Library/Frameworks/Python.framework/Versions/Current/bin/python3 python3; do
  if command -v "$cand" >/dev/null 2>&1 && \
     "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$(command -v "$cand")"
    break
  fi
done

if [ -z "$PY" ] && command -v brew >/dev/null 2>&1; then
  echo 'Python 3 was not found. Installing it with Homebrew...'
  brew install python@3.12
  PY="$(brew --prefix)/bin/python3.12"
fi
if [ -z "$PY" ]; then
  echo
  echo 'Python 3.9 or newer is needed. Install Python 3.12 from'
  echo '  https://www.python.org/downloads/macos/'
  echo 'then run this again:  bash setup_mac.sh'
  exit 1
fi
echo "Using Python: $PY ($("$PY" --version 2>&1))"

if [ ! -x .venv/bin/python ]; then
  echo 'Creating the virtual environment...'
  "$PY" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo 'Installing the Chromium browser used by the automation...'
.venv/bin/python -m playwright install chromium
echo done > .venv/.setup_complete

echo
echo 'Adding "Crisp Automation" to Applications...'
QT_QPA_PLATFORM=offscreen .venv/bin/python -m crisp_gui.desktop_shortcut || true

echo
echo 'Setup completed.'
echo 'Open "Crisp Automation" from Launchpad or Applications (or Spotlight: Cmd+Space, type Crisp).'
echo 'To keep it in the Dock: open it, then right-click its Dock icon > Options > Keep in Dock.'

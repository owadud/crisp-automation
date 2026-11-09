#!/usr/bin/env bash
# Start Crisp Automation on macOS from Terminal (normally you open the app instead).
set -Eeuo pipefail
cd "$(cd "$(dirname "$0")" && pwd)"
if [ ! -x .venv/bin/python ]; then
  echo 'First-time setup is required. Run: bash setup_mac.sh'
  exit 1
fi
# One-time: add "Crisp Automation" to Applications, so next time no Terminal is needed.
if [ ! -d "$HOME/Applications/Crisp Automation.app" ]; then
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m crisp_gui.desktop_shortcut || true
fi
exec .venv/bin/python gui_app.py

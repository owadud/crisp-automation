#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo 'First-time setup is required. Run: bash setup_ubuntu.sh'
  exit 1
fi
# One-time: add "Crisp Automation" to the app grid, so next time no terminal is needed.
if [ ! -f "$HOME/.local/share/applications/crisp-automation.desktop" ]; then
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m crisp_gui.desktop_shortcut || true
fi
exec .venv/bin/python gui_app.py

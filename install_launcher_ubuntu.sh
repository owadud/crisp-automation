#!/usr/bin/env bash
# One-time: add "Crisp Automation" to the Ubuntu app grid (no terminal needed afterwards).
#   bash install_launcher_ubuntu.sh
# (run_ubuntu.sh and Settings > Open like an app do the same.)
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo "Run the one-time setup first:  bash setup_ubuntu.sh"
  exit 1
fi
QT_QPA_PLATFORM=offscreen .venv/bin/python -m crisp_gui.desktop_shortcut

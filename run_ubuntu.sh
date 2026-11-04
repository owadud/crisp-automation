#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  echo 'First-time setup is required. Run: bash setup_ubuntu.sh'
  exit 1
fi
exec .venv/bin/python gui_app.py

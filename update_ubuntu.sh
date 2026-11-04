#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo 'This folder is not a Git clone yet. Use the GitHub clone instructions in README.md.'
  exit 1
fi
git pull --ff-only
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
echo 'Update completed.'

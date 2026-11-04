#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$(readlink -f "$0")")"

 echo '========================================='
 echo ' Crisp Automation - Ubuntu first-time setup'
 echo '========================================='

if ! command -v python3 >/dev/null 2>&1; then
  echo 'Python 3 is missing. Installing it...'
fi

sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv python3-pip \
  libxcb-cursor0 libxkbcommon-x11-0 libegl1 libgl1 \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
  libgbm1 libasound2t64 || sudo apt-get install -y libasound2

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m playwright install chromium

echo
echo 'Setup completed successfully.'
echo 'To launch the app, run: bash run_ubuntu.sh'
echo 'You do not need to run setup again unless dependencies change.'

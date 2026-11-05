#!/usr/bin/env bash
# One-time: add "Crisp Automation" to the Ubuntu app menu (no terminal needed afterwards).
#   bash install_launcher_ubuntu.sh
# Then open it from Activities / the app grid, right-click it in the dock > "Pin to Dash".
# Updates keep working: the launcher always runs run_ubuntu.sh from this folder.
set -Eeuo pipefail
APP_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons"
mkdir -p "$APPS" "$ICONS"

if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "Run the one-time setup first:  bash setup_ubuntu.sh"
  exit 1
fi

# App icon, drawn from the app's own logo.
cd "$APP_DIR"
QT_QPA_PLATFORM=offscreen "$APP_DIR/.venv/bin/python" - "$ICONS/crisp-automation.png" <<'PY' || true
import sys
sys.path.insert(0, ".")
from PySide6.QtWidgets import QApplication
app = QApplication([])
from crisp_gui.icons import logo_pixmap
logo_pixmap("#5b5bd6", 128).save(sys.argv[1])
PY

# Small wrapper: logs output and shows a popup if the app cannot start.
cat > "$APP_DIR/launch_ubuntu.sh" <<EOF
#!/usr/bin/env bash
cd "$APP_DIR"
LOG="\$HOME/.crisp_email_agent/launcher.log"
mkdir -p "\$(dirname "\$LOG")"
if ! bash "$APP_DIR/run_ubuntu.sh" >"\$LOG" 2>&1; then
  MSG="Crisp Automation could not start. Details: \$LOG"
  command -v zenity >/dev/null && zenity --error --title="Crisp Automation" --text="\$MSG" \\
    || notify-send "Crisp Automation" "\$MSG" 2>/dev/null || true
fi
EOF
chmod +x "$APP_DIR/launch_ubuntu.sh"

cat > "$APPS/crisp-automation.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Crisp Automation
Comment=Send Crisp email campaigns
Exec=bash "$APP_DIR/launch_ubuntu.sh"
Path=$APP_DIR
Icon=$ICONS/crisp-automation.png
Terminal=false
Categories=Office;Network;
StartupNotify=true
EOF
chmod +x "$APPS/crisp-automation.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true

echo "Done. Open 'Crisp Automation' from the app grid (Super key, type Crisp)."
echo "To keep it in the dock: open it, right-click its dock icon > Pin to Dash."

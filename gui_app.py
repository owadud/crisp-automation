#!/usr/bin/env python3
"""Crisp Automation Desktop: entry point.

Launched by run_ubuntu.sh / run_windows.bat exactly as before. The GUI lives
in the crisp_gui package; the automation itself stays in
crisp_email_agent.py, which this app runs as a child process.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

LOG = Path.home() / ".crisp_email_agent" / "app.log"


def _log_crash(text: str) -> None:
    """Started from a shortcut there is no terminal: keep the error in a file and show it."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S}\n{text}")
    except OSError:
        pass
    msg = f"Crisp Automation stopped because of an error.\n\nDetails were saved to:\n{LOG}\n\n{text[-1200:]}"
    if QApplication.instance() is not None:
        QMessageBox.critical(None, "Crisp Automation", msg)
    elif sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, msg, "Crisp Automation", 0x10)
    elif sys.platform == "darwin":
        import subprocess
        text = msg[:1500].replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(["osascript", "-e", f'display alert "Crisp Automation" message "{text}" as critical'],
                       capture_output=True, timeout=120)
    else:
        print(msg, file=sys.__stderr__ or sys.stdout)

from crisp_gui.config import APP_NAME, Preferences
from crisp_gui.main_window import MainWindow
from crisp_gui.theme import ThemeManager


def main() -> int:
    if sys.platform == "win32":
        try:   # taskbar shows the app's own icon, not Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CrispAutomation.Desktop")
        except Exception:  # noqa: BLE001
            pass
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CrispAutomation")
    app.setDesktopFileName("crisp-automation")  # matches the Ubuntu launcher (dock icon)
    app.setStyle("Fusion")  # consistent rendering on Windows and Ubuntu
    from crisp_gui.icons import logo_pixmap
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap("#5b5bd6", size))
    app.setWindowIcon(icon)

    prefs = Preferences()
    tm = ThemeManager(app, prefs.theme())
    font = QFont(tm.ui_font)
    font.setPixelSize(13)
    app.setFont(font)

    window = MainWindow(tm, prefs)
    window.show()
    window.check_updates_on_start()  # background, never blocks the window
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        _log_crash(traceback.format_exc())
        sys.exit(1)

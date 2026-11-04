#!/usr/bin/env python3
"""Crisp Automation Desktop: entry point.

Launched by run_ubuntu.sh / run_windows.bat exactly as before. The GUI lives
in the crisp_gui package; the automation itself stays in
crisp_email_agent.py, which this app runs as a child process.
"""

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from crisp_gui.config import APP_NAME, Preferences
from crisp_gui.main_window import MainWindow
from crisp_gui.theme import ThemeManager


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("CrispAutomation")
    app.setStyle("Fusion")  # consistent rendering on Windows and Ubuntu

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
    sys.exit(main())

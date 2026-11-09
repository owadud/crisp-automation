"""Open web links (Crisp conversations) in Google Chrome, where Crisp is usually
signed in. Falls back to the computer's default browser when Chrome is missing."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_chrome(env=os.environ, which=shutil.which, exists=lambda p: Path(p).is_file()) -> str:
    """Path of Google Chrome (or Chromium on Linux), or '' if not installed."""
    if sys.platform == "win32":
        for base in (env.get("ProgramFiles", r"C:\Program Files"), env.get("ProgramFiles(x86)", ""),
                     env.get("LOCALAPPDATA", "")):
            if base:
                path = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
                if exists(path):
                    return str(path)
        return which("chrome") or ""
    if sys.platform == "darwin":
        for app in ("/Applications/Google Chrome.app", str(Path.home() / "Applications" / "Google Chrome.app")):
            if exists(Path(app) / "Contents" / "MacOS" / "Google Chrome"):
                return app
        return ""
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = which(name)
        if found:
            return found
    return ""


def open_url(url: str, chrome: str | None = None, popen=subprocess.Popen, fallback=None) -> str:
    """Open the link in Chrome; returns 'chrome' or 'default'."""
    chrome = find_chrome() if chrome is None else chrome
    if chrome:
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            cmd = ["open", "-a", chrome, url] if chrome.endswith(".app") else [chrome, url]   # macOS: open -a
            popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  creationflags=flags if sys.platform == "win32" else 0,
                  start_new_session=sys.platform != "win32")
            return "chrome"
        except OSError:
            pass
    if fallback is None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl(url))
    else:
        fallback(url)
    return "default"

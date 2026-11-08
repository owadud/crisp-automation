"""Conversation links open in Google Chrome when it is installed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crisp_gui import browser  # noqa: E402

URL = "https://app.crisp.chat/website/w/inbox/session_x/"


def test_find_chrome_on_windows_and_linux(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "win32")
    path = str(Path(r"C:\Users\me\AppData\Local") / "Google" / "Chrome" / "Application" / "chrome.exe")
    found = browser.find_chrome(env={"LOCALAPPDATA": r"C:\Users\me\AppData\Local"}, which=lambda n: None,
                                exists=lambda p: str(p) == path)
    assert found == path
    monkeypatch.setattr(browser.sys, "platform", "linux")
    assert browser.find_chrome(which=lambda n: "/usr/bin/google-chrome-stable" if n == "google-chrome-stable"
                               else None) == "/usr/bin/google-chrome-stable"
    assert browser.find_chrome(which=lambda n: None) == ""


def test_open_url_prefers_chrome_and_falls_back():
    launched, fallback = [], []
    assert browser.open_url(URL, chrome="/usr/bin/google-chrome", popen=lambda cmd, **kw: launched.append(cmd),
                            fallback=fallback.append) == "chrome"
    assert launched == [["/usr/bin/google-chrome", URL]] and fallback == []
    assert browser.open_url(URL, chrome="", fallback=fallback.append) == "default" and fallback == [URL]

    def broken(cmd, **kw):
        raise OSError("gone")
    assert browser.open_url(URL, chrome="/x/chrome", popen=broken, fallback=fallback.append) == "default"

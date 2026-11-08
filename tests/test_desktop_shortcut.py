"""Open like an app: Windows shortcuts and the Ubuntu app-grid entry (no terminal)."""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crisp_gui import agent_process  # noqa: E402
from crisp_gui import desktop_shortcut as ds  # noqa: E402


def fake_app(tmp_path, windows):
    root = tmp_path / "My App (1)"
    exe = root / ".venv" / ("Scripts/pythonw.exe" if windows else "bin/python")
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    (root / "gui_app.py").write_text("")
    return root


def test_linux_launcher_opens_without_a_terminal(tmp_path, monkeypatch):
    monkeypatch.setattr(ds.sys, "platform", "linux")
    root, home = fake_app(tmp_path, windows=False), tmp_path / "home"
    made = ds.linux_launcher(root, home)
    text = made[0].read_text(encoding="utf-8")
    assert made[0] == home / ".local/share/applications/crisp-automation.desktop"
    assert "Terminal=false" in text and "StartupWMClass=crisp-automation" in text
    assert f'Exec="{root / ".venv/bin/python"}" "{root / "gui_app.py"}"' in text
    assert f"Path={root}" in text


def test_windows_shortcut_script_uses_pythonw_and_the_real_desktop(tmp_path, monkeypatch):
    root = fake_app(tmp_path, windows=True)
    monkeypatch.setattr(ds, "windowed_python", lambda r: r / ".venv" / "Scripts" / "pythonw.exe")
    monkeypatch.setattr(ds, "make_icon", lambda path, ico: None)
    seen = {}

    def run(cmd, **kw):
        seen["script"] = Path(cmd[-1]).read_text(encoding="utf-8-sig")
        seen["flags"] = kw.get("creationflags")
        out = "C:\\Users\\x\\OneDrive\\Desktop\\Crisp Automation.lnk\nC:\\Start\\Crisp Automation.lnk\n"
        return type("R", (), {"returncode": 0, "stdout": out, "stderr": ""})()
    made = ds.windows_shortcuts(root, run=run)
    s = seen["script"]
    assert "GetFolderPath('Desktop')" in s and "GetFolderPath('Programs')" in s   # OneDrive-safe
    assert "pythonw.exe" in s and f"\"{root / 'gui_app.py'}\"" in s
    assert "My App (1)" in s and "Test-Path" in s
    assert [p.name for p in made] == ["Crisp Automation.lnk", "Crisp Automation.lnk"]


def test_shortcut_needs_setup_first(tmp_path):
    with pytest.raises(RuntimeError, match="setup"):
        ds.windows_shortcuts(tmp_path) if sys.platform == "win32" else ds.linux_launcher(tmp_path, tmp_path)


def test_agent_runs_without_a_console_window_on_windows(tmp_path, monkeypatch):
    exe = tmp_path / "python.exe"
    exe.write_text("")
    (tmp_path / "pythonw.exe").write_text("")
    monkeypatch.setattr(agent_process.sys, "executable", str(exe))
    monkeypatch.setattr(agent_process.sys, "platform", "win32")
    assert agent_process.agent_python() == str(tmp_path / "pythonw.exe")
    monkeypatch.setattr(agent_process.sys, "platform", "linux")
    assert agent_process.agent_python() == str(exe)

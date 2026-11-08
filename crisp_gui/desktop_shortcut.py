"""Open the app like any other app: a Desktop / Start-menu shortcut on Windows,
an app-grid entry on Ubuntu. No terminal window.

The shortcuts point at THIS folder's virtual environment and gui_app.py, so
they keep working after in-app updates (code is replaced in place). Run again
after moving the folder.

    python -m crisp_gui.desktop_shortcut          (or the Settings button)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Crisp Automation"
APP_ID = "CrispAutomation.Desktop"     # Windows taskbar groups the window with its shortcut


def data_dir() -> Path:
    return Path.home() / ".crisp_email_agent"


def windowed_python(root: Path = ROOT) -> Path:
    """The interpreter that opens no console window (pythonw.exe on Windows)."""
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "pythonw.exe"
    return root / ".venv" / "bin" / "python"


def make_icon(path: Path, ico: bool) -> Path | None:
    """Draw the app logo into a .png or .ico. Returns None if Qt cannot draw it."""
    try:
        from PySide6.QtGui import QGuiApplication, QImage
        app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (needed to paint)
        from .icons import logo_pixmap
        path.parent.mkdir(parents=True, exist_ok=True)
        img: QImage = logo_pixmap("#5b5bd6", 256).toImage()
        if img.save(str(path), "ICO" if ico else "PNG"):
            return path
        if ico:   # no ICO writer: Windows shortcuts can use the python icon instead
            return None
    except Exception:  # noqa: BLE001
        return None
    return None


# ------------------------------------------------------------------ Windows
def _ps_quote(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def windows_shortcuts(root: Path = ROOT, desktop: bool = True, start_menu: bool = True,
                      run=subprocess.run) -> list[Path]:
    """Create 'Crisp Automation.lnk' on the Desktop and in the Start menu.
    Windows itself says where those folders are (the Desktop is often in OneDrive)."""
    target = windowed_python(root)
    if not target.exists():
        raise RuntimeError("Run the one-time setup first (setup_windows.bat).")
    icon = make_icon(data_dir() / "crisp-automation.ico", ico=True)
    folders = ([("Desktop", "Desktop")] if desktop else []) + ([("Programs", "Programs")] if start_menu else [])
    lines = ["$ErrorActionPreference = 'Stop'", "$ws = New-Object -ComObject WScript.Shell"]
    for _key, special in folders:
        lines += [f"$dir = [Environment]::GetFolderPath('{special}')",
                  "New-Item -ItemType Directory -Force -Path $dir | Out-Null",
                  f"$link = Join-Path $dir {_ps_quote(APP_NAME + '.lnk')}",
                  "$s = $ws.CreateShortcut($link)",
                  f"$s.TargetPath = {_ps_quote(target)}",
                  f"$s.Arguments = {_ps_quote(chr(34) + str(root / 'gui_app.py') + chr(34))}",
                  f"$s.WorkingDirectory = {_ps_quote(root)}",
                  f"$s.Description = {_ps_quote('Send Crisp email campaigns')}"]
        if icon:
            lines.append(f"$s.IconLocation = {_ps_quote(str(icon) + ',0')}")
        lines += ["$s.Save()",
                  "if (-not (Test-Path -LiteralPath $link)) { throw \"Shortcut not saved: $link\" }",
                  "Write-Output $link"]
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")
        script = f.name
    try:
        r = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                capture_output=True, text=True, timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        Path(script).unlink(missing_ok=True)
    if r.returncode != 0:
        raise RuntimeError(f"Could not create the shortcut: {(r.stderr or r.stdout).strip()[:300]}")
    return [Path(line.strip()) for line in r.stdout.splitlines() if line.strip().lower().endswith(".lnk")]


# ------------------------------------------------------------------ Ubuntu / Linux
def linux_launcher(root: Path = ROOT, home: Path | None = None) -> list[Path]:
    """~/.local/share/applications/crisp-automation.desktop (+ icon). Open it from
    the app grid, then right-click its dock icon > Pin to Dash."""
    home = home or Path.home()
    python = windowed_python(root)
    if not python.exists():
        raise RuntimeError("Run the one-time setup first: bash setup_ubuntu.sh")
    apps = home / ".local" / "share" / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    icon = make_icon(home / ".local" / "share" / "icons" / "crisp-automation.png", ico=False)
    desktop = apps / "crisp-automation.desktop"
    desktop.write_text("\n".join([
        "[Desktop Entry]",
        "Type=Application",
        f"Name={APP_NAME}",
        "Comment=Send Crisp email campaigns",
        f'Exec="{python}" "{root / "gui_app.py"}"',
        f"Path={root}",
        f"Icon={icon or 'mail-send'}",
        "Terminal=false",
        "Categories=Office;Network;",
        "StartupNotify=true",
        "StartupWMClass=crisp-automation",
        "",
    ]), encoding="utf-8")
    desktop.chmod(0o755)
    try:
        subprocess.run(["update-desktop-database", str(apps)], capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass
    return [desktop]


def install(root: Path = ROOT) -> list[Path]:
    if sys.platform == "win32":
        return windows_shortcuts(root)
    if sys.platform.startswith("linux"):
        return linux_launcher(root)
    raise RuntimeError("Shortcuts are made on Windows and Ubuntu only.")


def installed(root: Path = ROOT) -> bool:
    if sys.platform == "win32":
        return (Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / f"{APP_NAME}.lnk").exists()
    return (Path.home() / ".local" / "share" / "applications" / "crisp-automation.desktop").exists()


if __name__ == "__main__":
    try:
        for p in install():
            print(f"Created: {p}")
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)
    if sys.platform == "win32":
        print("Open 'Crisp Automation' from the Desktop or the Start menu. To pin it: right-click > Pin to taskbar.")
    else:
        print("Open 'Crisp Automation' from the app grid (Super key, type Crisp). "
              "To keep it in the dock: right-click its icon > Pin to Dash.")

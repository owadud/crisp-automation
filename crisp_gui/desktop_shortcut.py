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


def make_icon(path: Path, ico: bool = False) -> Path | None:
    """Draw the app logo into a .png, .ico (Windows) or .icns (macOS), by the file's
    extension. Returns None if Qt cannot draw or write it (the OS default icon is used)."""
    fmt = {".ico": "ICO", ".icns": "ICNS"}.get(Path(path).suffix.lower(), "ICO" if ico else "PNG")
    try:
        from PySide6.QtGui import QGuiApplication, QImage
        app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 (needed to paint)
        from .icons import logo_pixmap
        path.parent.mkdir(parents=True, exist_ok=True)
        img: QImage = logo_pixmap("#5b5bd6", 512 if fmt == "ICNS" else 256).toImage()
        if img.save(str(path), fmt):
            return path
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
    made = [desktop]
    # A Desktop icon too. GNOME opens it only when it is marked trusted
    # ("Allow Launching"), which gio does for us; without gio it asks once.
    desk = desktop_folder(home)
    if desk is not None:
        icon_link = desk / "crisp-automation.desktop"
        icon_link.write_text(desktop.read_text(encoding="utf-8"), encoding="utf-8")
        icon_link.chmod(0o755)
        for cmd in (["gio", "set", str(icon_link), "metadata::trusted", "true"],
                    ["gio", "set", "-t", "string", str(icon_link), "metadata::trusted", "true"]):
            try:
                if subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0:
                    break
            except (OSError, subprocess.TimeoutExpired):
                break
        made.append(icon_link)
    return made


def desktop_folder(home: Path) -> Path | None:
    """The user's Desktop folder (it has another name in other languages), if it exists."""
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=10).stdout.strip()
        if out and Path(out).is_dir() and Path(out) != home:
            return Path(out)
    except (OSError, subprocess.TimeoutExpired):
        pass
    fallback = home / "Desktop"
    return fallback if fallback.is_dir() else None


# ------------------------------------------------------------------ macOS
BUNDLE_ID = "com.crispautomation.desktop"


def mac_app(root: Path = ROOT, home: Path | None = None, register=None) -> list[Path]:
    """~/Applications/Crisp Automation.app: a small app bundle that starts this
    folder's app (no Terminal). It shows in Launchpad, Spotlight and Applications;
    keep it in the Dock with right-click > Options > Keep in Dock."""
    home = home or Path.home()
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        raise RuntimeError("Run the one-time setup first: bash setup_mac.sh")
    app = home / "Applications" / f"{APP_NAME}.app"
    contents = app / "Contents"
    (contents / "MacOS").mkdir(parents=True, exist_ok=True)
    (contents / "Resources").mkdir(parents=True, exist_ok=True)
    icon = make_icon(contents / "Resources" / "AppIcon.icns")
    plist = {
        "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": "crisp-automation", "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": _version(root), "CFBundleVersion": _version(root),
        "LSMinimumSystemVersion": "11.0", "NSHighResolutionCapable": True,
    }
    if icon:
        plist["CFBundleIconFile"] = "AppIcon"
    import plistlib
    (contents / "Info.plist").write_bytes(plistlib.dumps(plist))
    log = "$HOME/.crisp_email_agent/launcher.log"
    launcher = contents / "MacOS" / "crisp-automation"
    launcher.write_text("\n".join([
        "#!/bin/bash",
        "# Starts Crisp Automation from its folder (made by setup_mac.sh / Settings > Open like an app).",
        f'cd {_sh_quote(root)} || exit 1',
        f'mkdir -p "$HOME/.crisp_email_agent"',
        f'exec {_sh_quote(python)} {_sh_quote(root / "gui_app.py")} >>"{log}" 2>&1',
        "",
    ]), encoding="utf-8")
    launcher.chmod(0o755)
    (register or _register_mac_app)(app)
    return [app]


def _sh_quote(text) -> str:
    return "'" + str(text).replace("'", "'\\''") + "'"


def _version(root: Path) -> str:
    try:
        return (root / "VERSION").read_text(encoding="utf-8").strip() or "1.0"
    except OSError:
        return "1.0"


def _register_mac_app(app: Path) -> None:
    """Tell Launchpad / Spotlight about the app (and refresh its icon). Optional."""
    lsregister = ("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework"
                  "/Support/lsregister")
    for cmd in (["touch", str(app)], [lsregister, "-f", str(app)]):
        try:
            subprocess.run(cmd, capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ------------------------------------------------------------------ all
def install(root: Path = ROOT) -> list[Path]:
    if sys.platform == "win32":
        return windows_shortcuts(root)
    if sys.platform == "darwin":
        return mac_app(root)
    if sys.platform.startswith("linux"):
        return linux_launcher(root)
    raise RuntimeError("Shortcuts are made on Windows, macOS and Ubuntu only.")


def installed(root: Path = ROOT) -> bool:
    if sys.platform == "win32":
        return (Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / f"{APP_NAME}.lnk").exists()
    if sys.platform == "darwin":
        return (Path.home() / "Applications" / f"{APP_NAME}.app").exists()
    return (Path.home() / ".local" / "share" / "applications" / "crisp-automation.desktop").exists()


def where_text() -> str:
    if sys.platform == "win32":
        return "the Desktop and the Start menu. To pin it: right-click it > Pin to taskbar."
    if sys.platform == "darwin":
        return ("Launchpad or Applications (or Spotlight: Cmd+Space, type Crisp). To keep it in the Dock: "
                "right-click its Dock icon > Options > Keep in Dock.")
    return ("the Desktop or the app grid (Super key, type Crisp). If the Desktop icon shows a warning, "
            "right-click it > Allow Launching. To keep it in the dock: right-click > Pin to Dash.")


if __name__ == "__main__":
    try:
        for p in install():
            print(f"Created: {p}")
    except RuntimeError as exc:
        print(exc)
        sys.exit(1)
    print(f"Open 'Crisp Automation' from {where_text()}")

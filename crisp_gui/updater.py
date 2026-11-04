"""One-click update from GitHub Releases.

Flow: read the latest release -> download the zip -> verify sha256 ->
back up current code to backups/<old_version>/ -> replace ONLY code files ->
(re)install requirements if they changed -> start check -> done.
Any failure after the backup restores the old code automatically.

User data is never read or written: settings (QSettings live outside this
folder), CSVs, JSON, history, logs, keys, backups/, runs/, .venv/ and the
browser profile are all excluded below.

Pure Python (no Qt) so it can be tested without a GUI or network.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

USER_AGENT = "CrispAutomation-Updater"
API_LATEST = "https://api.github.com/repos/{repo}/releases/latest"

# Only these count as app code and may be replaced by an update.
CODE_SUFFIXES = {".py", ".sh", ".bat", ".md", ".txt", ".html"}
CODE_NAMES = {"VERSION", ".gitignore", ".gitattributes"}
# Never touched, wherever they appear.
PROTECTED_DIRS = {".venv", "venv", "backups", "runs", "logs", "dev", "dist", ".git", ".github", "sheet_cache", "diagnostics",
                  "browser_profile", ".crisp_email_agent", "__pycache__", ".pytest_cache"}
PROTECTED_SUFFIXES = {".csv", ".json", ".pem", ".key", ".log", ".lock", ".env"}
PROTECTED_PREFIXES = ("credentials", "service_account", ".env", "history", "debug_")

Opener = Callable[[str, str], bytes]


class UpdateError(Exception):
    """A problem the user should read. The message is shown as is."""


@dataclass
class Release:
    tag: str
    version: str
    zip_name: str
    zip_url: str
    sha256_url: str | None = None
    digest: str | None = None
    page_url: str = ""


# ------------------------------------------------------------------ versions
def parse_version(text: str) -> tuple[int, ...]:
    """'v3.1' -> (3, 1). Trailing zeros are ignored, so 3.0 == 3.0.0."""
    m = re.fullmatch(r"\s*[vV]?(\d+(?:\.\d+)*)\s*", str(text))
    if not m:
        raise ValueError(f"Not a version number: {text!r}")
    parts = [int(p) for p in m.group(1).split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def is_newer(remote: str, local: str) -> bool:
    """True only if remote is numerically newer. Unparseable -> False (safe)."""
    try:
        return parse_version(remote) > parse_version(local)
    except ValueError:
        return False


def same_version(a: str, b: str) -> bool:
    try:
        return parse_version(a) == parse_version(b)
    except ValueError:
        return False


# ------------------------------------------------------------------ network
def http_get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def fetch_latest_release(repo: str, opener: Opener = http_get) -> Release | None:
    """Latest published release, or None if the repo has none yet."""
    try:
        data = json.loads(opener(API_LATEST.format(repo=repo), "application/vnd.github+json"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise UpdateError(f"GitHub answered with error {exc.code}. Try again later.") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise UpdateError(f"Could not reach GitHub. Check the internet connection. ({exc})") from exc
    tag = str(data.get("tag_name") or "")
    assets = data.get("assets") or []
    zips = [a for a in assets if str(a.get("name", "")).endswith(".zip")]
    if not tag or not zips:
        raise UpdateError(f"Release {tag or '(no tag)'} has no .zip file attached.")
    z = zips[0]
    sha_asset = next((a for a in assets if a.get("name") == f"{z['name']}.sha256"), None)
    digest = str(z.get("digest") or "")
    return Release(
        tag=tag,
        version=tag.lstrip("vV"),
        zip_name=z["name"],
        zip_url=z["browser_download_url"],
        sha256_url=sha_asset["browser_download_url"] if sha_asset else None,
        digest=digest.split(":", 1)[1] if digest.startswith("sha256:") else None,
        page_url=str(data.get("html_url") or ""),
    )


def expected_sha256(release: Release, opener: Opener = http_get) -> str:
    """The published checksum. Refuses to continue without one."""
    if release.sha256_url:
        text = opener(release.sha256_url, "application/octet-stream").decode("utf-8", "replace")
        value = text.split()[0] if text.split() else ""
    else:
        value = release.digest or ""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise UpdateError(f"Release {release.tag} has no valid sha256 checksum, so it was not installed.")
    return value.lower()


def download(release: Release, dest_dir: Path, opener: Opener = http_get) -> Path:
    path = Path(dest_dir) / release.zip_name
    try:
        path.write_bytes(opener(release.zip_url, "application/octet-stream"))
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"Download failed. Check the internet connection. ({exc})") from exc
    return path


# ------------------------------------------------------------------ files
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def is_protected(rel: PurePosixPath) -> bool:
    if any(part in PROTECTED_DIRS for part in rel.parts[:-1]):
        return True
    name = rel.name.lower()
    return rel.suffix.lower() in PROTECTED_SUFFIXES or name.startswith(PROTECTED_PREFIXES)


def is_code_file(rel: PurePosixPath) -> bool:
    if is_protected(rel):
        return False
    return rel.name in CODE_NAMES or rel.suffix.lower() in CODE_SUFFIXES


def list_code_files(root: Path) -> list[PurePosixPath]:
    root = Path(root)
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in PROTECTED_DIRS]
        for name in filenames:
            rel = PurePosixPath(Path(dirpath, name).relative_to(root).as_posix())
            if is_code_file(rel):
                found.append(rel)
    return sorted(found)


def extract_release(zip_path: Path, dest: Path) -> Path:
    """Unzip safely and return the folder that holds VERSION and gui_app.py."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for name in zf.namelist():
                p = PurePosixPath(name.replace("\\", "/"))
                if p.is_absolute() or ".." in p.parts or ":" in name:
                    raise UpdateError(f"The update zip contains an unsafe path ({name}). Not installed.")
            zf.extractall(dest)
    except zipfile.BadZipFile as exc:
        raise UpdateError("The downloaded file is not a valid zip. Not installed.") from exc
    entries = list(Path(dest).iterdir())
    root = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(dest)
    if not (root / "VERSION").is_file() or not (root / "gui_app.py").is_file():
        raise UpdateError("The zip is not a Crisp Automation release (VERSION or gui_app.py missing).")
    return root


def make_backup(root: Path, version: str) -> Path:
    base = Path(root) / "backups"
    target = base / re.sub(r"[^0-9A-Za-z._-]", "_", version or "unknown")
    if target.exists():
        target = base / f"{target.name}_{time.strftime('%Y%m%d-%H%M%S')}"
    for rel in list_code_files(root):
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(Path(root) / rel, dst)
    return target


def restore_backup(root: Path, backup: Path, created: list[PurePosixPath]) -> None:
    root = Path(root)
    for rel in created:
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            pass
        parent = (root / rel).parent
        while parent != root and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    for rel in list_code_files(backup):
        dst = root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup / rel, dst)


# ------------------------------------------------------------------ checks
def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


SMOKE_CODE = (
    "import pathlib\n"
    "for p in [pathlib.Path('crisp_email_agent.py'), *pathlib.Path('crisp_gui').glob('*.py')]:\n"
    "    compile(p.read_text(encoding='utf-8'), str(p), 'exec')\n"
    "import gui_app, crisp_gui.main_window\n"
)


def smoke_check(root: Path) -> tuple[bool, str]:
    """Compile the agent and import the GUI in a separate Python, without showing a window."""
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run([sys.executable, "-c", SMOKE_CODE], cwd=root, env=env, capture_output=True,
                           text=True, timeout=180, creationflags=_no_window())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    return r.returncode == 0, (r.stdout + r.stderr)


def install_requirements(root: Path) -> tuple[bool, str]:
    out = []
    for args in (["-m", "pip", "install", "-r", "requirements.txt"],
                 ["-m", "playwright", "install", "chromium"]):
        try:
            r = subprocess.run([sys.executable, *args], cwd=root, capture_output=True, text=True,
                               timeout=1800, creationflags=_no_window())
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, str(exc)
        out.append(r.stdout + r.stderr)
        if r.returncode != 0:
            return False, "\n".join(out)
    return True, "\n".join(out)


# ------------------------------------------------------------------ install
def _requirements(root: Path) -> str:
    """requirements.txt content; read_text() already ignores CRLF vs LF."""
    p = Path(root) / "requirements.txt"
    return p.read_text(encoding="utf-8").strip() if p.exists() else ""


def install_update(zip_path: Path, sha256: str, root: Path, current_version: str,
                   expected_version: str | None = None,
                   check: Callable[[Path], tuple[bool, str]] = smoke_check,
                   install_deps: Callable[[Path], tuple[bool, str]] = install_requirements,
                   log: Callable[[str], None] = lambda _msg: None) -> Path:
    """Install the zip into root. Returns the backup folder.

    Nothing is changed if the checksum or zip is bad. After the backup, any
    failure restores the previous code and raises UpdateError.
    """
    root = Path(root)
    log("Checking the download…")
    if sha256_file(zip_path) != sha256.lower():
        raise UpdateError("Checksum mismatch: the download is damaged or was changed. Nothing was installed.")
    with tempfile.TemporaryDirectory(prefix="crisp_update_") as tmp:
        new_root = extract_release(zip_path, Path(tmp))
        new_version = (new_root / "VERSION").read_text(encoding="utf-8").strip()
        if expected_version and not same_version(new_version, expected_version):
            raise UpdateError(f"The zip says version {new_version}, expected {expected_version}. Nothing was installed.")
        new_files = list_code_files(new_root)
        old_reqs = _requirements(root)

        log(f"Backing up version {current_version}…")
        backup = make_backup(root, current_version)
        created: list[PurePosixPath] = []
        try:
            log("Replacing code files…")
            for rel in new_files:
                dst = root / rel
                if not dst.exists():
                    created.append(rel)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(new_root / rel, dst)
            if _requirements(root) != old_reqs:
                log("Installing updated Python packages (this can take a few minutes)…")
                ok, output = install_deps(root)
                if not ok:
                    raise UpdateError("Installing packages failed:\n" + output[-800:])
            log("Checking that the new version starts…")
            ok, output = check(root)
            if not ok:
                raise UpdateError("The new version failed its start check:\n" + output[-800:])
        except Exception as exc:
            try:
                restore_backup(root, backup, created)
            except Exception as rb_exc:  # noqa: BLE001
                raise UpdateError(f"Update failed AND the automatic restore failed ({rb_exc}). "
                                  f"Copy the files from {backup} back into {root} by hand.") from exc
            raise UpdateError(f"Update failed, so version {current_version} was restored. "
                              f"Nothing else changed.\n\nReason: {exc}") from exc
    log("Update installed.")
    return backup

"""Runs updater.py work on a background thread so the window never freezes.

Signals are delivered to the receiver's (GUI) thread, so connect them to
methods of QObjects (e.g. MainWindow), not to lambdas.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from . import updater


class UpdateController(QObject):
    checked = Signal(object, str)   # Release | None, status text
    check_failed = Signal(str)
    progress = Signal(str)
    installed = Signal(str)         # backup folder
    install_failed = Signal(str)

    def __init__(self, repo: str, root: Path, version: str, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.root = Path(root)
        self.version = version
        self.release: updater.Release | None = None
        self.busy = False

    def _start(self, target) -> bool:
        if self.busy:
            return False
        self.busy = True
        threading.Thread(target=target, daemon=True).start()
        return True

    # check ---------------------------------------------------------------
    def check(self) -> bool:
        return self._start(self._check)

    def _check(self):
        try:
            rel = updater.fetch_latest_release(self.repo)
            if rel is None:
                self.release = None
                msg = "No release has been published yet."
            elif updater.is_newer(rel.version, self.version):
                self.release = rel
                msg = f"Version {rel.version} is available (you have {self.version})."
            else:
                self.release = None
                msg = f"You have the latest version ({self.version})."
        except updater.UpdateError as exc:
            self.busy = False
            self.check_failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.busy = False
            self.check_failed.emit(f"Update check failed: {exc}")
            return
        self.busy = False
        self.checked.emit(self.release, msg)

    # install -------------------------------------------------------------
    def install(self) -> bool:
        if self.release is None:
            return False
        return self._start(self._install)

    def _install(self):
        rel = self.release
        try:
            with tempfile.TemporaryDirectory(prefix="crisp_download_") as tmp:
                self.progress.emit(f"Downloading version {rel.version}…")
                sha = updater.expected_sha256(rel)
                zip_path = updater.download(rel, Path(tmp))
                backup = updater.install_update(zip_path, sha, self.root, self.version,
                                                expected_version=rel.version, log=self.progress.emit)
        except updater.UpdateError as exc:
            self.busy = False
            self.install_failed.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            self.busy = False
            self.install_failed.emit(f"Update failed before anything was changed: {exc}")
            return
        self.busy = False
        self.installed.emit(str(backup))

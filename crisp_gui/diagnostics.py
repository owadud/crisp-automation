"""'Copy diagnostics': one zip a teammate can send for help.

Contains a masked log, versions, masked settings and self-test results.
Never includes the Crisp browser profile, CSV files, screenshots or keys.
"""

from __future__ import annotations

import json
import platform
import re
import shutil
import sys
import zipfile
from datetime import datetime
from pathlib import Path

_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def mask_emails(text: str) -> str:
    """'jane.doe@example.com' -> 'j***@example.com'."""
    return _EMAIL.sub(r"\1***@\2", text or "")


def _mask_path(value: str) -> str:
    return Path(value).name if value else ""


def versions(root: Path) -> dict:
    info = {"app": (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").exists() else "?",
            "python": sys.version.split()[0], "os": f"{platform.system()} {platform.release()}",
            "machine": platform.machine()}
    for mod in ("PySide6", "playwright"):
        try:
            from importlib.metadata import version
            info[mod] = version(mod)
        except Exception:
            info[mod] = "not installed"
    return info


def self_test(root: Path, csv_path: str, profile_dir: Path, history_file: Path) -> list[tuple[str, bool, str]]:
    results = []

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append((name, bool(ok), mask_emails(str(detail))))

    def agent_compiles():
        compile((root / "crisp_email_agent.py").read_text(encoding="utf-8"), "crisp_email_agent.py", "exec")
        return True, "compiles"

    def playwright_ok():
        import importlib.util
        return importlib.util.find_spec("playwright") is not None, "importable"

    def csv_ok():
        if not csv_path:
            return False, "no CSV chosen"
        from .csv_stats import read_stats
        st = read_stats(csv_path, "")
        return st.ok, st.error or f"{st.total} rows readable"

    def disk_ok():
        free = shutil.disk_usage(root).free / 1e9
        return free > 1, f"{free:.1f} GB free"

    check("Agent script", agent_compiles)
    check("Playwright", playwright_ok)
    check("Recipient CSV", csv_ok)
    check("Browser profile folder", lambda: (Path(profile_dir).exists(), "exists" if Path(profile_dir).exists()
                                             else "not created yet"))
    check("History file", lambda: (True, "present" if Path(history_file).exists() else "none yet"))
    check("Disk space", disk_ok)
    return results


def build_zip(dest_dir: Path, root: Path, log_text: str, settings: dict, profile_dir: Path,
              history_file: Path) -> Path:
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"diagnostics-{datetime.now():%Y-%m-%d_%H%M%S}.zip"
    safe_settings = {k: (_mask_path(v) if k in ("csv", "dnc") else mask_emails(str(v))) for k, v in settings.items()}
    tests = self_test(root, settings.get("csv", ""), profile_dir, history_file)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("log_masked.txt", mask_emails(log_text))
        zf.writestr("versions.json", json.dumps(versions(root), indent=2))
        zf.writestr("settings_masked.json", json.dumps(safe_settings, indent=2, ensure_ascii=False))
        zf.writestr("self_test.txt", "\n".join(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}"
                                               for name, ok, detail in tests))
    return path

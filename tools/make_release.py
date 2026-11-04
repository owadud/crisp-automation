"""Build the release zip and its sha256 file.

Only files tracked by git are packed (so CSVs, logs, keys, .venv and dev/
can never be included), minus a second safety filter below.

    python tools/make_release.py            # uses VERSION
    python tools/make_release.py --tag v3.1 # also checks the tag matches VERSION

Output: dist/crisp-automation-v<version>.zip and .zip.sha256
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "Crisp_Email_Agent"  # folder name inside the zip, same as the manual download
EXCLUDE = ["dev/*", ".github/*", "tools/*", "dist/*", "backups/*", "runs/*", "logs/*",
           "*.csv", "*.json", "*.log", "*.lock", "*.pem", "*.key", "*.zip", "*.backup",
           "debug_*", "*/debug_*", "credentials*", "service_account*", ".env*"]
KEEP = {"recipients_template.csv"}


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return sorted(p for p in out.decode("utf-8").split("\0") if p)


def excluded(rel: str) -> bool:
    return rel not in KEEP and any(fnmatch.fnmatch(rel, pat) for pat in EXCLUDE)


def build(root: Path = ROOT, tag: str | None = None) -> tuple[Path, str]:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if tag and tag.lstrip("vV") != version:
        raise SystemExit(f"Tag {tag} does not match VERSION {version}. Edit VERSION or use tag v{version}.")
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    name = f"crisp-automation-v{version}.zip"
    zip_path = dist / name
    files = [f for f in tracked_files(root) if not excluded(f) and (root / f).is_file()]
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            zf.write(root / rel, f"{TOP}/{rel}")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    (dist / f"{name}.sha256").write_text(f"{sha}  {name}\n", encoding="utf-8")
    print(f"{zip_path} ({len(files)} files)\nsha256 {sha}")
    return zip_path, sha


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag")
    build(tag=ap.parse_args().tag)
    sys.exit(0)

"""Updater: version compare, checksum failure, rollback. No network, no Qt."""

import hashlib
import json
import sys
import urllib.error
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crisp_gui import updater as up  # noqa: E402


def make_app(root: Path) -> Path:
    files = {
        "VERSION": "3.0\n",
        "gui_app.py": "old gui\n",
        "crisp_gui/config.py": "old config\n",
        "requirements.txt": "pyside6\n",
        "recipients.csv": "Full Name,Contact Email\nA,a@x.com\n",
        "history.csv": "old history\n",
        "settings.json": "{}",
        ".venv/lib/site.py": "venv code\n",
        "runs/r1/run.log": "log\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def make_zip(path: Path, version="3.1", extra=None) -> tuple[Path, str]:
    files = {
        "VERSION": f"{version}\n",
        "gui_app.py": "new gui\n",
        "crisp_gui/config.py": "new config\n",
        "crisp_gui/updater_new.py": "brand new\n",
        "requirements.txt": "pyside6\n",
        "recipients_template.csv": "Full Name,Contact Email\n",
    }
    files.update(extra or {})
    with zipfile.ZipFile(path, "w") as zf:
        for rel, text in files.items():
            zf.writestr(f"Crisp_Email_Agent/{rel}", text)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def ok_check(_root):
    return True, "ok"


def no_deps(_root):
    raise AssertionError("requirements did not change, pip must not run")


def read(root, rel):
    return (root / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("remote, local, newer", [
    ("v3.1", "3.0", True),
    ("3.0", "3.0", False),
    ("v3.0.0", "3.0", False),
    ("3.10", "3.9", True),
    ("3.0.1", "3.0", True),
    ("2.9", "3.0", False),
    ("latest", "3.0", False),
])
def test_version_compare(remote, local, newer):
    assert up.is_newer(remote, local) is newer


def test_fetch_latest_release_parses_assets_and_handles_no_release():
    payload = {"tag_name": "v3.1", "html_url": "u", "assets": [
        {"name": "crisp-automation-v3.1.zip", "browser_download_url": "https://x/zip"},
        {"name": "crisp-automation-v3.1.zip.sha256", "browser_download_url": "https://x/sha"},
    ]}
    rel = up.fetch_latest_release("o/r", lambda url, accept: json.dumps(payload).encode())
    assert (rel.version, rel.zip_url, rel.sha256_url) == ("3.1", "https://x/zip", "https://x/sha")

    def not_found(url, accept):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
    assert up.fetch_latest_release("o/r", not_found) is None

    sha = "a" * 64
    assert up.expected_sha256(rel, lambda url, accept: f"{sha}  file.zip\n".encode()) == sha


def test_install_replaces_code_only_and_keeps_user_data(tmp_path):
    root = make_app(tmp_path / "app")
    zip_path, sha = make_zip(tmp_path / "u.zip")
    backup = up.install_update(zip_path, sha, root, "3.0", expected_version="3.1",
                               check=ok_check, install_deps=no_deps)
    assert read(root, "VERSION").strip() == "3.1"
    assert read(root, "gui_app.py") == "new gui\n"
    assert read(root, "crisp_gui/updater_new.py") == "brand new\n"
    # user data untouched, template CSV not written
    assert "a@x.com" in read(root, "recipients.csv")
    assert read(root, "history.csv") == "old history\n"
    assert read(root, ".venv/lib/site.py") == "venv code\n"
    assert not (root / "recipients_template.csv").exists()
    # backup holds the old code
    assert backup == root / "backups" / "3.0"
    assert read(backup, "gui_app.py") == "old gui\n"
    assert not (backup / "recipients.csv").exists()


def test_checksum_failure_changes_nothing(tmp_path):
    root = make_app(tmp_path / "app")
    zip_path, _ = make_zip(tmp_path / "u.zip")
    with pytest.raises(up.UpdateError, match="Checksum mismatch"):
        up.install_update(zip_path, "0" * 64, root, "3.0", check=ok_check, install_deps=no_deps)
    assert read(root, "gui_app.py") == "old gui\n"
    assert not (root / "backups").exists()


def test_failed_start_check_rolls_back(tmp_path):
    root = make_app(tmp_path / "app")
    zip_path, sha = make_zip(tmp_path / "u.zip")
    with pytest.raises(up.UpdateError, match="3.0 was restored"):
        up.install_update(zip_path, sha, root, "3.0", check=lambda r: (False, "ImportError: boom"),
                          install_deps=no_deps)
    assert read(root, "VERSION").strip() == "3.0"
    assert read(root, "gui_app.py") == "old gui\n"
    assert read(root, "crisp_gui/config.py") == "old config\n"
    assert not (root / "crisp_gui/updater_new.py").exists()
    assert "a@x.com" in read(root, "recipients.csv")


def test_failed_package_install_rolls_back(tmp_path):
    root = make_app(tmp_path / "app")
    zip_path, sha = make_zip(tmp_path / "u.zip", extra={"requirements.txt": "pyside6\nnewpkg\n"})
    with pytest.raises(up.UpdateError, match="Installing packages failed"):
        up.install_update(zip_path, sha, root, "3.0", check=ok_check,
                          install_deps=lambda r: (False, "no network"))
    assert read(root, "requirements.txt") == "pyside6\n"
    assert read(root, "gui_app.py") == "old gui\n"


def test_unsafe_zip_and_version_mismatch_are_refused(tmp_path):
    root = make_app(tmp_path / "app")
    zip_path, sha = make_zip(tmp_path / "evil.zip")
    with zipfile.ZipFile(zip_path, "a") as zf:
        zf.writestr("../evil.py", "x")
    sha = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    with pytest.raises(up.UpdateError, match="unsafe path"):
        up.install_update(zip_path, sha, root, "3.0", check=ok_check, install_deps=no_deps)

    zip_path, sha = make_zip(tmp_path / "u.zip", version="3.2")
    with pytest.raises(up.UpdateError, match="expected 3.1"):
        up.install_update(zip_path, sha, root, "3.0", expected_version="3.1",
                          check=ok_check, install_deps=no_deps)
    assert read(root, "gui_app.py") == "old gui\n"

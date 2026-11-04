"""Runs the REAL crisp_email_agent.py through the GUI runner.

Crisp itself isn't reachable from a test machine, so these cover the paths
that don't need a Crisp login: startup errors, no eligible rows, and
stopping while the real Chromium window waits for login.
The Chromium test needs a display (run under xvfb-run on Linux CI).
"""

import csv
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from crisp_gui import run_state as rs  # noqa: E402
from crisp_gui.agent_process import AgentRunner  # noqa: E402
from crisp_gui.config import AGENT_SCRIPT, CAMPAIGN_DEFAULTS  # noqa: E402

pytest.importorskip("playwright")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def settings(csv_path):
    v = dict(CAMPAIGN_DEFAULTS)
    v["csv"] = str(csv_path)
    return v


def run(app, runner, timeout):
    lines = []
    runner.line.connect(lines.append)
    end = time.time() + timeout
    while runner.is_running() and time.time() < end:
        QApplication.processEvents()
        QTest.qWait(30)
    QApplication.processEvents()
    return lines


def test_missing_csv_exits_instead_of_hanging(app, tmp_path):
    """Before the fix, the agent waited forever on input('Press ENTER')."""
    r = AgentRunner(AGENT_SCRIPT)
    assert r.start(settings(tmp_path / "gone.csv"), True, tmp_path / "p") is None
    t = time.time()
    lines = run(app, r, 30)
    assert not r.is_running(), "agent hung"
    assert time.time() - t < 30
    assert r.proc.exitCode() == 1
    assert r.state.phase == rs.FAILED
    assert "CSV file not found" in r.state.last_error
    assert any(line.startswith("ERROR:") for line in lines)


def test_no_eligible_recipients(app, tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("Full Name,Contact Email,Reference,Mail Sent\nA,a@x.com,Shahriar,TRUE\n", encoding="utf-8")
    r = AgentRunner(AGENT_SCRIPT)
    r.start(settings(p), True, tmp_path / "p")
    run(app, r, 30)
    assert r.state.phase == rs.FINISHED and r.state.no_eligible
    assert r.state.eligible == 0


def chromium_pids(profile):
    out = subprocess.run(["pgrep", "-f", str(profile)], capture_output=True, text=True).stdout
    return [p for p in out.split() if p]


@pytest.mark.skipif(not os.environ.get("DISPLAY"), reason="needs a display (xvfb-run)")
def test_stop_while_real_chromium_waits_for_login(app, tmp_path):
    p = tmp_path / "r.csv"
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Full Name", "Contact Email", "Reference", "Mail Sent"])
        w.writerow(["A", "a@example.com", "Shahriar", ""])
    profile = tmp_path / "profile"
    r = AgentRunner(AGENT_SCRIPT)
    r.start(settings(p), True, profile)
    end = time.time() + 60
    while time.time() < end and not (r.state.phase == rs.WAITING_LOGIN and chromium_pids(profile)):
        QApplication.processEvents()
        QTest.qWait(100)
    assert r.state.phase == rs.WAITING_LOGIN, r.state
    assert chromium_pids(profile), "Chromium did not start"
    assert profile.exists()
    assert r.request_stop() == "killed"      # nothing in flight yet → immediate
    run(app, r, 15)
    assert not r.is_running()
    assert r.state.phase == rs.STOPPED
    # the browser must not be left behind
    end = time.time() + 10
    while chromium_pids(profile) and time.time() < end:
        time.sleep(0.3)
    assert not chromium_pids(profile), "Chromium left running after Stop"
    assert p.read_text(encoding="utf-8-sig").strip().endswith(",")   # CSV untouched

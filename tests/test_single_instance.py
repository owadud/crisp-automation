"""Opening the app twice brings the first window forward instead of a second copy."""

import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from crisp_gui import single_instance as si  # noqa: E402


def test_second_launch_asks_the_running_copy_to_show():
    app = QCoreApplication.instance() or QCoreApplication([])
    name = f"crisp-test-{uuid.uuid4().hex[:8]}"
    assert si.ask_running_copy_to_show(name, timeout_ms=200) is False   # nothing running yet
    seen = []
    server = si.InstanceServer(seen.append, name=name)
    code = (f"import sys; sys.path.insert(0, {str(ROOT)!r}); from PySide6.QtCore import QCoreApplication; "
            f"a = QCoreApplication([]); from crisp_gui import single_instance as si; "
            f"sys.exit(0 if si.ask_running_copy_to_show({name!r}) else 1)")
    proc = subprocess.Popen([sys.executable, "-c", code], env={"XDG_ACTIVATION_TOKEN": "tok-1",
                                                               "PATH": "/usr/bin:/bin"})
    deadline = time.time() + 10
    while not seen and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert proc.wait(10) == 0
    assert seen == ["tok-1"]
    server.server.close()

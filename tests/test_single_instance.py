import os
import subprocess
import sys
import time
import uuid

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer

from locallens.single_instance import SingleInstance


def wait_until(application, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out while waiting for local IPC")


def test_second_instance_notifies_primary_and_exits():
    application = QApplication.instance() or QApplication([])
    server_name = f"LocalLens.Test.{uuid.uuid4().hex}"
    primary = SingleInstance(server_name, connection_timeout_ms=500)
    activations = []
    event_loop = QEventLoop()

    def record_activation():
        activations.append(True)
        event_loop.quit()

    primary.activation_requested.connect(record_activation)

    assert primary.acquire() is True
    assert primary.is_primary is True

    code = (
        "from PySide6.QtCore import QCoreApplication; "
        "from locallens.single_instance import SingleInstance; "
        "app=QCoreApplication([]); "
        f"instance=SingleInstance({server_name!r}, connection_timeout_ms=3000); "
        "print(instance.acquire(), instance.is_primary, flush=True)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=os.getcwd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    QTimer.singleShot(5000, event_loop.quit)
    event_loop.exec()
    stdout, stderr = process.communicate(timeout=3)

    assert process.returncode == 0, stderr
    assert stdout.strip() == "False False"
    assert activations == [True]

    primary.close()


def test_server_name_can_be_acquired_again_after_clean_exit():
    application = QApplication.instance() or QApplication([])
    server_name = f"LocalLens.Test.{uuid.uuid4().hex}"
    first = SingleInstance(server_name, connection_timeout_ms=500)
    replacement = SingleInstance(server_name, connection_timeout_ms=500)

    assert first.acquire() is True
    first.close()
    application.processEvents()

    assert replacement.acquire() is True
    replacement.close()

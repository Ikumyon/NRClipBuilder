import os
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def wait_until(predicate, seconds=10):
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(20)
    raise AssertionError("Timed out waiting for asynchronous operation")


def javascript(page, expression):
    result = []
    page.runJavaScript(expression, lambda value: result.append(value))
    wait_until(lambda: bool(result))
    return result[0]


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    from nrclip_builder.services import ApplicationService
    from nrclip_builder.storage import SessionRepository
    from nrclip_builder.ui.window import MainWindow
    errors = []
    monkeypatch.setattr(MainWindow, "error", lambda self, message: errors.append(str(message)))
    service = ApplicationService(repository=SessionRepository(tmp_path))
    for layer in service.state.layers:
        if layer.kind == "tile":
            layer.visible = False
    view = MainWindow(service)
    view.show()
    wait_until(lambda: service.initialized and view.map.map_ready)
    yield view
    wait_until(lambda: not service.jobs.inflight and not service.edit_busy)
    view.close_allowed = True
    view.close()
    service.jobs.pool.waitForDone()
    assert not errors

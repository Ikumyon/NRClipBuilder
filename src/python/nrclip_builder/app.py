import sys
import argparse

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="NRClipBuilder")
    parser.add_argument("--version", action="version", version=f"NRClipBuilder {__version__}")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()
    from . import _native
    monitor_startup, supervised_result = _native.supervise_startup()
    if supervised_result is not None:
        return supervised_result
    _native.initialize()

    from PySide6.QtCore import QTimer
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWidgets import QApplication

    from .ui.window import MainWindow
    from .ui.icons import application_icon

    app = QApplication(sys.argv)
    app.setApplicationName("NRClipBuilder")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("NRClipBuilder")
    app.setWindowIcon(application_icon())
    window = MainWindow()
    window.setWindowIcon(app.windowIcon())
    startup_complete = False

    def startup_ready():
        nonlocal startup_complete
        _native.startup_ready()
        startup_complete = True

    def map_event(event):
        if event["type"] == "ready":
            # Let the first frames paint before declaring graphics startup healthy.
            QTimer.singleShot(1000, startup_ready)

    def renderer_terminated(status, _exit_code):
        if not startup_complete and status != QWebEnginePage.RenderProcessTerminationStatus.NormalTerminationStatus:
            app.exit(_native.GRAPHICS_STARTUP_FAILURE)

    if monitor_startup:
        window.map.event.connect(map_event)
        window.map.map_page.renderProcessTerminated.connect(renderer_terminated)
        if window.map.map_ready:
            QTimer.singleShot(1000, startup_ready)
    paths = args.paths
    if paths:
        window.service.ready.connect(lambda: [window.service.load(path) for path in paths])
    window.show()
    result = app.exec()
    window.service.jobs.pool.waitForDone(40000)
    return result

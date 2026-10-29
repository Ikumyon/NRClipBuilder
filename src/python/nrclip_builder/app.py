import sys
import argparse
import tempfile

from . import __version__


def main():
    parser = argparse.ArgumentParser(description="NRClipBuilder")
    parser.add_argument("--version", action="version", version=f"NRClipBuilder {__version__}")
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--smoke-test", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    from PySide6.QtWidgets import QApplication

    from . import _native
    from .ui.window import MainWindow
    from .services import ApplicationService
    from .storage import SessionRepository
    from .ui.icons import application_icon

    _native.initialize()
    app = QApplication(sys.argv)
    app.setApplicationName("NRClipBuilder")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("NRClipBuilder")
    app.setWindowIcon(application_icon())
    temporary = tempfile.TemporaryDirectory(prefix="nrclip-smoke-") if args.smoke_test else None
    service = ApplicationService(repository=SessionRepository(temporary.name)) if temporary else None
    window = MainWindow(service, show_errors=not args.smoke_test)
    window.setWindowIcon(app.windowIcon())
    paths = args.paths
    if paths:
        window.service.ready.connect(lambda: [window.service.load(path) for path in paths])
    window.show()
    if temporary:
        from .smoke import install
        smoke_timer = install(app, window, temporary.name)
    result = app.exec()
    window.service.jobs.pool.waitForDone(40000)
    if temporary:
        temporary.cleanup()
    return result

"""CI-only entry point; never included in the distribution application."""
import sys
import tempfile
import traceback

from nrclip_builder import _native


def main():
    _, result = _native.supervise_startup()
    if result is not None:
        return result
    _native.initialize()

    from PySide6.QtWidgets import QApplication
    from nrclip_builder.app import main as application_main
    from nrclip_builder.services import ApplicationService
    from nrclip_builder.storage import SessionRepository
    from nrclip_builder.ui import window
    from smoke import install

    with tempfile.TemporaryDirectory(prefix="nrclip-smoke-") as directory:
        original_window = window.MainWindow

        class SmokeWindow(original_window):
            def __init__(self):
                service = ApplicationService(repository=SessionRepository(directory))
                super().__init__(service)
                self.smoke_timer = install(QApplication.instance(), self, directory)

            def error(self, message):
                self.statusBar().showMessage(str(message))
                print(message, flush=True)

        window.MainWindow = SmokeWindow
        try:
            return application_main()
        finally:
            window.MainWindow = original_window


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)

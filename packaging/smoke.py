"""Exercise the actual packaged application without changing the user's session."""
from pathlib import Path
from os import environ
from time import monotonic

from PySide6.QtCore import QTimer

from nrclip_builder import __version__


def install(app, window, directory):
    service = window.service
    deadline = monotonic() + 45
    stage = 0
    errors = []
    expected_version = environ.get("NRCLIP_EXPECTED_VERSION", __version__)
    if __version__ != expected_version or app.applicationVersion() != expected_version:
        errors.append(f"Version mismatch: expected {expected_version}, got {__version__}")
    if not window.windowTitle().endswith(f" {expected_version}"):
        errors.append("Window title does not contain the embedded version")
    rendered = []
    service.failed.connect(errors.append)
    window.map.failed.connect(errors.append)
    window.map.event.connect(lambda event: rendered.append(True) if event["type"] == "rendered" else None)
    timer = QTimer(window)
    timer.setInterval(50)

    def check():
        nonlocal stage
        if errors or monotonic() > deadline:
            print("SMOKE FAILED:", errors or "timeout", flush=True)
            timer.stop()
            service.jobs.cancel()
            window.close_allowed = True
            window.close()
            app.exit(1)
            return
        if stage == 0 and service.initialized and window.map.map_ready:
            feature = {"type": "Feature", "id": "smoke-line", "properties": {"railway": "rail", "name": "日本語"},
                       "geometry": {"type": "LineString", "coordinates": [[139.7,35.6],[139.71,35.61]]}}
            service.add_features("smoke", [feature])
            service.set_bbox([139,35,140,36])
            stage = 1
        elif stage == 1 and any(layer.kind == "data" for layer in service.state.layers):
            service.export(Path(directory) / "smoke.nrclip", "nrclip", {}, service.prepare_export())
            stage = 2
        elif stage == 2 and service.state.history and rendered and not service.jobs.active:
            service.save()
            stage = 3
        elif stage == 3 and not service.jobs.active and service.repository.path.exists():
            restored = service.repository.read()
            assert restored[0].history and restored[1]
            assert (Path(directory) / "smoke.nrclip").read_bytes().startswith(b"NRC1")
            window.grab().save(str(Path(directory) / "smoke.png"))
            print("SMOKE OK: native export, WebChannel map, session recovery, bundled resources", flush=True)
            timer.stop()
            window.close_allowed = True
            window.close()
            app.exit(0)
    def tick():
        try:
            check()
        except Exception as exc:
            errors.append(str(exc))
    timer.timeout.connect(tick)
    timer.start()
    return timer

"""Child process fixture for the Rust startup supervisor; requires no Qt or GPU."""
import os
from pathlib import Path
import sys
import time

scenario, trace = sys.argv[1:]
flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
mode = os.environ.get("NRCLIP_TEST_MODE") or (
    "software" if "--disable-gpu" in flags else "egl" if "--use-gl=egl" in flags else "default"
)
with Path(trace).open("a", encoding="utf-8") as stream:
    stream.write(mode + "\n")

if scenario == "unrelated-error":
    raise SystemExit(1)
if scenario == "clean-exit":
    raise SystemExit(0)
if scenario in {"signal", "terminated"} and mode == "default":
    import resource
    import signal
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.kill(os.getpid(), signal.SIGABRT if scenario == "signal" else signal.SIGTERM)
if scenario == "timeout" and mode == "default":
    time.sleep(20)
if scenario == mode or scenario == "late-failure" or scenario in {"timeout", "signal"}:
    Path(os.environ["NRCLIP_STARTUP_READY"]).write_bytes(b"ready")
    raise SystemExit(86 if scenario == "late-failure" else 0)
raise SystemExit(86)

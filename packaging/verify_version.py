"""Check the installed wheel and Windows executable against the intended version."""

import argparse
from importlib.metadata import version
from pathlib import Path
import sys

from packaging.version import Version


def verify(expected, executable=None):
    from nrclip_builder import __version__, _native

    assert __version__ == _native.__version__ == expected, (
        f"Embedded version mismatch: {__version__!r}, expected {expected!r}"
    )
    assert Version(version("nrclip-builder")) == Version(expected), "Wheel metadata mismatch"
    if sys.platform == "win32" and executable:
        from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable

        info = read_version_info_from_executable(str(Path(executable).resolve()))
        assert info is not None, "Executable has no Windows version resource"
        strings = info.kids[0].kids[0].kids
        fields = {item.name: item.val for item in strings}
        assert fields["FileVersion"] == fields["ProductVersion"] == expected
        major, minor, patch = map(int, expected.split("-")[0].split("."))
        assert info.ffi.fileVersionMS == info.ffi.productVersionMS == (major << 16 | minor)
        assert info.ffi.fileVersionLS == info.ffi.productVersionLS == patch << 16
    print(f"Verified application, wheel and executable version: {expected}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("expected")
    parser.add_argument("--executable")
    args = parser.parse_args()
    verify(args.expected, args.executable)

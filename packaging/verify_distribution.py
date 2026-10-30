"""Reject test files and embedded test modules in the production application."""
import argparse
from pathlib import Path
from zipfile import ZipFile

from PyInstaller.archive.readers import CArchiveReader


def is_test_item(name):
    parts = name.replace("\\", "/").replace(".", "/").lower().split("/")
    return any(
        part in {"test", "tests", "fixtures", "pytest", "unittest", "doctest", "smoke", "smoke_launcher", "qttest", "qt6test", "libqt6test", "qt6quicktest", "libqt6quicktest"}
        or part.startswith("test_")
        or part.endswith("_test")
        or part == "conftest"
        for part in parts
    )


def verify(executable):
    forbidden = [
        str(path.relative_to(executable.parent))
        for path in executable.parent.rglob("*")
        if is_test_item(str(path.relative_to(executable.parent)))
    ]
    archive = CArchiveReader(str(executable))
    for path in executable.parent.rglob("*.zip"):
        with ZipFile(path) as bundled:
            forbidden.extend(f"{path.name}:{name}" for name in bundled.namelist() if is_test_item(name))
    forbidden.extend(name for name in archive.toc if is_test_item(name))
    for name in archive.toc:
        if name.endswith(".pyz"):
            modules = archive.open_embedded_archive(name).toc
            forbidden.extend(name for name in modules if is_test_item(name))
    if forbidden:
        raise RuntimeError("Test items found in distribution: " + ", ".join(sorted(set(forbidden))))
    print(f"Distribution contains no test files or embedded test modules: {executable}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path)
    verify(parser.parse_args().executable)

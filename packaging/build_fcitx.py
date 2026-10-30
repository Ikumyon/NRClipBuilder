"""Build the Fcitx 4/5 input plugin against the exact Qt version shipped by PySide6."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request

from PySide6.QtCore import QLibraryInfo, QPluginLoader, qVersion


SOURCE_COMMIT = "115750044f3d0db16ca5859808c7da830fd1ff52"  # fcitx5-qt 5.1.16
SOURCE_SHA256 = "08415f72b79eeeb776f815f001cf42d4f00a238a91f5c4829b86bda009f2214a"
SOURCE_URL = f"https://github.com/fcitx/fcitx5-qt/archive/{SOURCE_COMMIT}.tar.gz"


def main():
    if sys.platform != "linux":
        raise SystemExit("The Fcitx plugin must be built on Linux.")
    root = Path(__file__).resolve().parents[1] / "build" / "fcitx"
    root.mkdir(parents=True, exist_ok=True)
    archive = root / "fcitx5-qt-5.1.16.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError(f"Fcitx source checksum mismatch: {archive}")
    with tarfile.open(archive, "r:gz") as source:
        source.extractall(root, filter="data")
    source = root / f"fcitx5-qt-{SOURCE_COMMIT}"

    # Qt's private input-context API requires the same version as the wheel.
    # The distro's Qt development package can be a different minor version.
    version = qVersion()
    sdk = root / "qt-sdk"
    configs = list((sdk / version).rglob("Qt6Config.cmake"))
    if not configs:
        subprocess.run([
            sys.executable, "-m", "aqt", "install-qt", "linux", "desktop",
            version, "linux_gcc_64", "--outputdir", str(sdk),
            "--archives", "qtbase", "qtwayland", "icu",
        ], check=True)
        configs = list((sdk / version).rglob("Qt6Config.cmake"))
    if len(configs) != 1:
        raise RuntimeError(f"Expected one Qt {version} SDK, found {configs}")
    prefix = configs[0].parents[3]
    output = root / f"plugin-{version}"
    subprocess.run([
        "cmake", "-S", str(source), "-B", str(output),
        f"-DCMAKE_PREFIX_PATH={prefix}", "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_ONLY_PLUGIN=ON", "-DENABLE_QT4=OFF", "-DENABLE_QT5=OFF",
        "-DENABLE_QT6=ON", "-DENABLE_X11=ON",
        "-DENABLE_QT6_WAYLAND_WORKAROUND=ON",
        "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON",
        "-DCMAKE_INSTALL_RPATH=$ORIGIN/../../lib",
    ], check=True)
    subprocess.run([
        "cmake", "--build", str(output), "--parallel", "2",
        "--target", "fcitx5platforminputcontextplugin-qt6",
    ], check=True)
    plugin = output / "qt6/platforminputcontext/libfcitx5platforminputcontextplugin.so"
    destination = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforminputcontexts" / plugin.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(plugin, destination)

    # Load with the wheel's Qt, not the SDK, to catch private ABI or missing-library errors.
    loader = QPluginLoader(str(destination))
    keys = loader.metaData().get("MetaData", {}).get("Keys", [])
    if "fcitx" not in keys or not loader.load():
        raise RuntimeError(f"Fcitx plugin cannot load with PySide6 Qt {version}: {keys}; {loader.errorString()}")
    loader.unload()
    print(f"Fcitx 4/5 plugin verified with Qt {version}: {destination}")


if __name__ == "__main__":
    main()

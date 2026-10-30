# Build on the destination OS; all application resource paths are package-relative.
from pathlib import Path
from runpy import run_path
from os import environ, name as os_name
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from nrclip_builder import _native
from PySide6.QtCore import QLibraryInfo

version_info = None
if os_name == 'nt':
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )
    app_version = _native.__version__
    numeric_version = tuple(int(part) for part in app_version.split('-')[0].split('.')) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=numeric_version, prodvers=numeric_version,
                          mask=0x3f, flags=0x2 if '-' in app_version else 0,
                          OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'NRClipBuilder'),
            StringStruct('FileVersion', app_version),
            StringStruct('ProductName', 'NRClipBuilder'),
            StringStruct('ProductVersion', app_version),
            StringStruct('OriginalFilename', 'NRClipBuilder.exe'),
        ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])],
    )

root = Path(SPECPATH).parent
is_test_item = run_path(str(root / 'packaging' / 'verify_distribution.py'))['is_test_item']
smoke_build = globals().get('_NRCLIP_SMOKE_BUILD', False)
datas = collect_data_files('nrclip_builder', excludes=['**/*.pdb', '**/tests/**', '**/fixtures/**', '**/smoke.py']) + collect_data_files('pyproj')
datas += [(str(root / 'LICENSE'), '.'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.')]
binaries = collect_dynamic_libs('pyproj')
if os_name != 'nt':
    fcitx = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / 'platforminputcontexts' / 'libfcitx5platforminputcontextplugin.so'
    if not fcitx.is_file():
        raise RuntimeError('Fcitx input plugin is missing. Run python packaging/build_fcitx.py before packaging on Linux.')
    binaries.append((str(fcitx), 'PySide6/Qt/plugins/platforminputcontexts'))
    fcitx_root = root / 'build' / 'fcitx'
    datas += [
        (str(fcitx_root / 'fcitx5-qt-5.1.16.tar.gz'), 'third_party/fcitx5-qt'),
        (str(fcitx_root / 'fcitx5-qt-115750044f3d0db16ca5859808c7da830fd1ff52' / 'LICENSES'), 'third_party/fcitx5-qt/LICENSES'),
    ]
a = Analysis(
    [str(root / 'packaging' / ('smoke_launcher.py' if smoke_build else 'launcher.py'))],
    # Bundle the installed wheel, including its matching native extension.
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=['nrclip_builder._native', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel'],
    excludes=['pytest', 'tests', 'zstandard', 'unittest', 'doctest', 'PySide6.QtTest'] + ([] if smoke_build else ['smoke', 'nrclip_builder.smoke']),
    noarchive=False,
)
if not smoke_build:
    a.pure = [entry for entry in a.pure if not is_test_item(entry[0])]
a.datas = [entry for entry in a.datas if not is_test_item(entry[0])]
a.binaries = [entry for entry in a.binaries if not is_test_item(entry[0]) and _native.bundle_library(Path(entry[0]).name)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='NRClipBuilder',
          version=version_info,
          icon=str(root / 'src' / 'python' / 'nrclip_builder' / 'resources' / 'assets' / 'icon.ico'),
          console=smoke_build or environ.get('NRCLIP_CONSOLE') == '1', strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='NRClipBuilder')

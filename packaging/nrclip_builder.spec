# Build on the destination OS; all application resource paths are package-relative.
from pathlib import Path
from os import environ, name as os_name
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs
from nrclip_builder import _native

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
datas = collect_data_files('nrclip_builder', excludes=['**/*.pdb']) + collect_data_files('pyproj')
datas += [(str(root / 'LICENSE'), '.'), (str(root / 'THIRD_PARTY_NOTICES.md'), '.')]
a = Analysis(
    [str(root / 'packaging' / 'launcher.py')],
    # Bundle the installed wheel, including its matching native extension.
    pathex=[],
    binaries=collect_dynamic_libs('pyproj'),
    datas=datas,
    hiddenimports=['nrclip_builder._native', 'PySide6.QtWebEngineWidgets', 'PySide6.QtWebChannel'],
    excludes=['pytest', 'tests', 'zstandard'],
    noarchive=False,
)
pyz = PYZ(a.pure)
a.binaries = [entry for entry in a.binaries if _native.bundle_library(Path(entry[0]).name)]
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='NRClipBuilder',
          version=version_info,
          icon=str(root / 'src' / 'python' / 'nrclip_builder' / 'resources' / 'assets' / 'icon.ico'),
          console=environ.get('NRCLIP_CONSOLE') == '1', strip=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='NRClipBuilder')

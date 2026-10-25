# -*- mode: python ; coding: utf-8 -*-
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

os.environ['QT_API'] = 'pyside6'
root = Path(SPECPATH)
hiddenimports = collect_submodules('bleak') + collect_submodules('winrt')
a = Analysis(
    [str(root / 'qt_app.py')],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / 'assets' / 'blescope.svg'), 'assets')],
    hiddenimports=hiddenimports,
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'tkinter', 'matplotlib', 'scipy',
              'pandas', 'IPython', 'numba', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineQuick',
              'PySide6.QtPdf', 'PySide6.QtPdfWidgets', '__main__'],
    noarchive=False, optimize=0,
)
# Qt uses Windows ICU. Exclude incompatible same-name copies found on PATH.
a.binaries = [entry for entry in a.binaries
              if entry[0].replace('\\', '/').split('/')[-1].lower() != 'icuuc.dll']
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='BLEScope', icon=str(root / 'assets' / 'blescope.ico'),
    debug=False, bootloader_ignore_signals=False,
    strip=False, upx=True, upx_exclude=[], runtime_tmpdir=None,
    console=False, disable_windowed_traceback=False,
    argv_emulation=False, target_arch=None,
    codesign_identity=None, entitlements_file=None,
)

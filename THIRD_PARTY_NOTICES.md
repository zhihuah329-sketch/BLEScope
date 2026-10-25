# Third-party notices

BLEScope's own source code is provided under the MIT license. Dependencies retain
their own licenses; the MIT license does not replace their terms.

| Dependency | Project | License |
|---|---|---|
| PySide6 / Qt for Python | https://www.qt.io/qt-for-python | LGPL-3.0 / GPL-3.0 / commercial, depending on component |
| Qt | https://www.qt.io/licensing/open-source-lgpl-obligations | LGPL-3.0 / GPL-3.0 / commercial, depending on component |
| Shiboken6 | https://doc.qt.io/qtforpython-6/shiboken6/ | LGPL-3.0 / GPL-3.0 / commercial |
| pyqtgraph | https://www.pyqtgraph.org/ | MIT |
| NumPy | https://numpy.org/ | BSD-3-Clause, plus bundled third-party notices |
| Bleak | https://github.com/hbldh/bleak | MIT |
| PyInstaller | https://pyinstaller.org/ | GPL with the bootloader exception |
| Python | https://www.python.org/ | PSF license |

This application uses dynamically linked Qt libraries. Source distributions include
the application code, dependency requirements and the PyInstaller build specification
so the application can be rebuilt with compatible replacement libraries. Windows
release archives include collected dependency license texts in `third_party_licenses/`.

CH9141 is a WCH device. BLEScope is an independent project and is not an official WCH
or Qt product. CH9141 support uses the public BLE GATT interface; no vendor firmware
or SDK binary is included in the repository.

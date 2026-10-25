"""Build a Windows executable and a clean portable archive."""
import importlib.metadata
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERSION = "0.4.0"


def make_icon():
    from PySide6 import QtCore, QtGui, QtSvg
    image = QtGui.QImage(256, 256, QtGui.QImage.Format.Format_ARGB32)
    image.fill(QtCore.Qt.GlobalColor.transparent)
    renderer = QtSvg.QSvgRenderer(str(ROOT / "assets" / "blescope.svg"))
    painter = QtGui.QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(ROOT / "assets" / "blescope.ico"), "ICO"):
        raise RuntimeError("Could not write application icon")


def collect_licenses(destination):
    for project in ("PySide6-Essentials", "shiboken6", "pyqtgraph", "numpy", "bleak", "pyinstaller"):
        distribution = importlib.metadata.distribution(project)
        for file in distribution.files or []:
            if not any("license" in part.lower() or part.lower().startswith(("copying", "notice")) for part in file.parts):
                continue
            source = Path(distribution.locate_file(file))
            if not source.is_file():
                continue
            safe_parts = [part for part in file.parts if part not in ("..", ".")]
            target = destination / project / Path(*safe_parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.exists():
        shutil.copy2(python_license, destination / "Python-LICENSE.txt")


def main():
    if sys.platform != "win32":
        raise SystemExit("Windows executable builds must run on Windows")
    os.chdir(ROOT)
    make_icon()
    environment = {**os.environ, "QT_API": "pyside6"}
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                    "--distpath", "dist", "--workpath", "build", "BLEScope.spec"],
                   env=environment, check=True)
    package = ROOT / "release" / f"BLEScope-{VERSION}-Windows-x64"
    package.mkdir(parents=True, exist_ok=True)
    for file in ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md"):
        shutil.copy2(ROOT / file, package / file)
    shutil.copy2(ROOT / "dist" / "BLEScope.exe", package / "BLEScope.exe")
    collect_licenses(package / "third_party_licenses")
    archive = shutil.make_archive(str(package), "zip", root_dir=package.parent, base_dir=package.name)
    print(archive)


if __name__ == "__main__":
    main()

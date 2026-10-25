import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import unittest
from types import SimpleNamespace
from PySide6 import QtCore as C, QtWidgets as W, QtTest
from qt_app import DeviceComboBox, BLEScope


class DeviceSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = W.QApplication.instance() or W.QApplication([])

    def setUp(self):
        self.combo = DeviceComboBox()
        self.combo.resize(210, 40)
        self.combo.show()

    def tearDown(self):
        self.combo.close()
        self.combo.deleteLater()
        self.app.processEvents()

    def test_click_opens_popup_and_keyboard_selects_second_address(self):
        self.combo.addItem("未命名设备  -62dBm\nAA:00", "AA:00")
        self.combo.addItem("CH9141BLE2U  -70dBm\nBB:11", "BB:11")
        self.combo.setCurrentIndex(0)
        QtTest.QTest.mouseClick(self.combo, C.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.combo.view().isVisible())
        QtTest.QTest.keyClick(self.combo, C.Qt.Key.Key_Down)
        QtTest.QTest.keyClick(self.combo, C.Qt.Key.Key_Return)
        self.assertEqual(self.combo.currentData(), "BB:11")
        self.combo.setCurrentIndex(0)
        QtTest.QTest.mouseClick(self.combo, C.Qt.MouseButton.LeftButton)
        self.app.processEvents()
        view = self.combo.view()
        position = view.visualRect(self.combo.model().index(1, 0)).center()
        QtTest.QTest.mouseClick(view.viewport(), C.Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(self.combo.currentData(), "BB:11")

    def test_population_retains_selection_and_reports_count(self):
        self.combo.addItem("CH9141", "BB:11")
        self.combo.setCurrentIndex(0)
        target = SimpleNamespace(device_combo=self.combo, devices=[], scanning=False,
                                 scan_feedback=W.QLabel(), connect_button=W.QPushButton())
        devices = [{"name": "未命名设备", "rssi": -62, "address": "AA:00"},
                   {"name": "CH9141BLE2U", "rssi": -70, "address": "BB:11"}]
        BLEScope.populate_devices(target, devices)
        self.assertEqual(self.combo.currentData(), "BB:11")
        self.assertIn("2", target.scan_feedback.text())
        self.assertIn("CH9141", target.scan_feedback.text())
        self.assertTrue(target.connect_button.isEnabled())
        BLEScope.populate_devices(target, [])
        self.assertFalse(target.connect_button.isEnabled())
        self.assertIn("没有发现", target.scan_feedback.text())


if __name__ == "__main__":
    unittest.main()

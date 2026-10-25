"""BLEScope: BLE telemetry, parameter debugging and signal inspection."""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W

from session import Session, start_api
from transport import Transport, NOTIFY_UUID, WRITE_UUID
from qt_waveform import x_coordinates

BASE = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
COLORS = ["#2970ef", "#08a588", "#e99524", "#af67d5", "#e16c8c", "#5c8d9e", "#85932d", "#d56942"]
STATUS = {"confirmed": "设备已确认", "pending": "等待设备确认", "timeout": "超时 / 生效未知",
          "rejected": "设备拒绝", "send_failed": "发送失败", "disconnected_unknown": "断连 / 生效未知"}
STYLE = """
QMainWindow, QWidget#body { background:#f2f5fa; }
QWidget { color:#243249; font-family:'Microsoft YaHei UI'; font-size:12px; }
QFrame#sidebar { background:#122239; border:0; }
QFrame#sidebar QLabel { color:#acbbcf; }
QLabel#brand { color:white; font-size:23px; font-weight:700; }
QLabel#brandmark { color:white; background:#377df2; border-radius:10px; font-size:23px; font-weight:700; }
QLabel#eyebrow { color:#7f98b8; font-size:10px; letter-spacing:2px; }
QLabel#title { font-size:24px; font-weight:700; color:#182b45; }
QLabel#subtitle { color:#8190a4; font-size:12px; }
QLabel#section { font-size:14px; font-weight:700; }
QLabel#muted { color:#8794a6; font-size:11px; }
QLabel#metric { font-size:24px; font-family:'Consolas'; font-weight:700; color:#1b3455; }
QLabel#badge { background:#e0f4ee; color:#168a71; border-radius:12px; padding:6px 14px; font-weight:600; }
QLabel#notice { background:#edf3ff; color:#526b90; border-radius:7px; padding:8px 12px; }
QFrame#card { background:white; border:1px solid #e5ebf3; border-radius:12px; }
QPushButton { background:white; border:1px solid #dde5ef; border-radius:7px; padding:8px 14px; color:#415571; }
QPushButton:hover { background:#f0f5fe; border-color:#a8c4f1; }
QPushButton:pressed { background:#e4edfd; }
QPushButton:disabled { color:#a1adbc; background:#f4f6f9; border-color:#ebeff4; }
QPushButton#primary { background:#3478ee; border:1px solid #3478ee; color:white; font-weight:600; }
QPushButton#primary:hover { background:#2366db; }
QPushButton#nav { background:transparent; border:0; text-align:left; color:#aabbcf; padding:12px 16px; font-size:13px; }
QPushButton#nav:checked { background:#223c5e; color:#fff; border-left:3px solid #70a5ff; border-radius:5px; }
QPushButton#side { background:#223a56; border:1px solid #304b6c; color:#deebfa; }
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox { background:#f8fafd; border:1px solid #dee6ef; border-radius:6px; padding:6px 9px; selection-background-color:#377df2; min-height:20px; }
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus { border-color:#6096ef; }
QComboBox::drop-down { border:0; width:20px; }
QComboBox QAbstractItemView { background:white; color:#243249; selection-background-color:#e4edfc; }
QFrame#sidebar QComboBox { background:#203650; border-color:#304863; color:white; }
QCheckBox { spacing:7px; }
QCheckBox::indicator { width:14px; height:14px; border-radius:4px; border:1px solid #c8d4e3; background:white; }
QCheckBox::indicator:checked { background:#3478ee; border-color:#3478ee; }
QHeaderView::section { background:#f6f8fc; border:0; border-bottom:1px solid #e6ecf4; padding:11px; color:#8794a6; text-align:left; }
QTableWidget { background:white; border:0; gridline-color:#eef2f7; selection-background-color:#eaf1ff; selection-color:#243249; }
QPlainTextEdit { border:1px solid #e5ebf3; border-radius:8px; background:#f8fafd; padding:12px; font-family:'Consolas'; font-size:11px; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { width:7px; background:#f0f3f8; }
QScrollBar::handle:vertical { background:#cbd7e7; border-radius:3px; min-height:30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


def label(text, name=None):
    widget = W.QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, handler, primary=False):
    widget = W.QPushButton(text)
    if primary:
        widget.setObjectName("primary")
    widget.clicked.connect(handler)
    return widget


def card():
    frame = W.QFrame()
    frame.setObjectName("card")
    layout = W.QVBoxLayout(frame)
    layout.setContentsMargins(20, 17, 20, 17)
    layout.setSpacing(12)
    return frame, layout


class DeviceComboBox(W.QComboBox):
    """Keep the entire selector clickable and paint a visible chevron."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(C.Qt.CursorShape.PointingHandCursor)
        self.setPlaceholderText("请先扫描蓝牙设备")
        self.setMinimumHeight(40)
        view = W.QListView()
        view.setMinimumWidth(430)
        view.setSpacing(3)
        view.setStyleSheet("QListView { background:white; color:#243249; padding:6px; border:1px solid #c9d7ea; } QListView::item { min-height:46px; padding:7px 12px; border-radius:5px; } QListView::item:selected { background:#e6efff; color:#205ec5; }")
        self.setView(view)
        self.setMaxVisibleItems(9)

    def paintEvent(self, event):
        painter = W.QStylePainter(self)
        option = W.QStyleOptionComboBox()
        self.initStyleOption(option)
        option.currentText = option.currentText.split("\n")[0]
        option.currentText = G.QFontMetrics(self.font()).elidedText(option.currentText, C.Qt.TextElideMode.ElideRight, self.width() - 38)
        painter.drawComplexControl(W.QStyle.ComplexControl.CC_ComboBox, option)
        painter.drawControl(W.QStyle.ControlElement.CE_ComboBoxLabel, option)
        painter.setRenderHint(G.QPainter.RenderHint.Antialiasing)
        painter.setPen(G.QPen(G.QColor("#dfebfc"), 1.8))
        x, y = self.width() - 16, self.height() // 2
        painter.drawLine(x - 4, y - 2, x, y + 2)
        painter.drawLine(x, y + 2, x + 4, y - 2)

    def mousePressEvent(self, event):
        if event.button() == C.Qt.MouseButton.LeftButton:
            if self.count():
                self.showPopup()
            event.accept()
        else:
            super().mousePressEvent(event)


class BLEScope(W.QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle("BLEScope 0.4.0 · BLE 调试工作台")
        self.setWindowIcon(G.QIcon(str(Path(__file__).resolve().parent / "assets" / "blescope.svg")))
        self.resize(1440, 920)
        self.setMinimumSize(1120, 750)
        self.setStyleSheet(STYLE)
        self.session = Session(BASE / "sessions")
        self.transport = Transport(self.session)
        self.session.start_maintenance()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="export")
        self.api = None
        self.closed = False
        self.devices = []
        self.scanning = False
        self.connecting = False
        self.scan_started = 0
        self.curves = {}
        self.checks = {}
        self.readouts = {}
        self.snapshot_cache = {}
        self.last_signature = None
        self.last_status = 0
        self.last_frames = 0
        self.fps_started = time.monotonic()
        self.fps = 0
        self.frames = 0
        self.last_frame_time = time.monotonic()
        self.display_end = None
        self.target_end = None
        self.pan_start = None
        self.paused = False
        self.frozen = []
        self.log_signature = None
        self.param_signature = None
        self.data = []
        self.last_plot_ms = 0
        self.build()
        try:
            self.api = start_api(self.session, 8767)
        except OSError as exc:
            self.session.event("api_unavailable", str(exc))
            self.statusBar().showMessage("8767接口不可用；本地文件记录仍可用。请关闭重复窗口。")
        self.timer = C.QTimer(self)
        self.timer.setTimerType(C.Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(16)
        if demo:
            self.transport.submit(self.transport.demo())

    def build(self):
        root = W.QWidget()
        root.setObjectName("body")
        self.setCentralWidget(root)
        outer = W.QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = W.QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(244)
        left = W.QVBoxLayout(side)
        left.setContentsMargins(22, 27, 22, 21)
        left.setSpacing(12)
        brand = W.QHBoxLayout()
        mark = label("B", "brandmark")
        mark.setFixedSize(38, 38)
        mark.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(mark)
        brand.addWidget(label("BLEScope", "brand"))
        left.addLayout(brand)
        left.addWidget(label("BLE SIGNAL LAB  /  04", "eyebrow"))
        left.addSpacing(26)
        self.nav = []
        for i, title in enumerate(["◉    实时波形", "≡    参数调试", "∿    滤波实验", "▤    会话与日志"]):
            item = button(title, lambda checked=False, index=i: self.navigate(index))
            item.setObjectName("nav")
            item.setCheckable(True)
            item.setChecked(i == 0)
            self.nav.append(item)
            left.addWidget(item)
        left.addSpacing(24)
        left.addWidget(label("设备连接", "eyebrow"))
        self.connection_text = label("尚未连接设备")
        self.connection_text.setWordWrap(True)
        left.addWidget(self.connection_text)
        self.scan_button = button("扫描蓝牙设备", self.scan)
        self.scan_button.setObjectName("side")
        left.addWidget(self.scan_button)
        self.scan_feedback = label("点击扫描，结果显示在下方。")
        self.scan_feedback.setWordWrap(True)
        self.scan_feedback.setStyleSheet("color:#9db4d3;font-size:11px;")
        left.addWidget(self.scan_feedback)
        self.device_combo = DeviceComboBox()
        self.device_combo.setMinimumContentsLength(10)
        self.device_combo.setSizeAdjustPolicy(W.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        left.addWidget(self.device_combo)
        self.connect_button = button("连接选中设备", self.connect_device, True)
        self.connect_button.setEnabled(False)
        self.device_combo.currentIndexChanged.connect(lambda index: self.connect_button.setEnabled(index >= 0 and not self.scanning and not self.connecting))
        left.addWidget(self.connect_button)
        disconnect = button("断开连接", lambda: self.transport.submit(self.transport.disconnect()))
        disconnect.setObjectName("side")
        left.addWidget(disconnect)
        advanced = button("高级 UUID 设置", self.advanced)
        advanced.setObjectName("side")
        left.addWidget(advanced)
        left.addStretch()
        demo = button("演示模式", lambda: self.transport.submit(self.transport.demo()))
        demo.setObjectName("side")
        left.addWidget(demo)
        left.addWidget(label("本地处理 · 数据自动记录\nBLE / FFF1 → FFF2", "eyebrow"))
        outer.addWidget(side)

        content = W.QVBoxLayout()
        content.setContentsMargins(30, 25, 30, 12)
        content.setSpacing(18)
        head = W.QHBoxLayout()
        heading = W.QVBoxLayout()
        self.page_title = label("信号工作台", "title")
        self.page_description = label("BLE 遥测、参数与信号分析，一站式调试。", "subtitle")
        heading.addWidget(self.page_title)
        heading.addWidget(self.page_description)
        head.addLayout(heading)
        head.addStretch()
        self.badge = label("○  离线", "badge")
        head.addWidget(self.badge)
        head.addWidget(button("导出分析包  ↗", self.export, True))
        content.addLayout(head)
        self.stack = W.QStackedWidget()
        self.stack.addWidget(self.build_scope())
        self.stack.addWidget(self.build_parameters())
        self.stack.addWidget(self.build_filters())
        self.stack.addWidget(self.build_logs())
        content.addWidget(self.stack, 1)
        footer = W.QHBoxLayout()
        footer.addWidget(label("QT + PYQTGRAPH   /   本地只读 API :8767", "muted"))
        footer.addStretch()
        self.fps_label = label("60 FPS 目标刷新", "muted")
        footer.addWidget(self.fps_label)
        content.addLayout(footer)
        outer.addLayout(content, 1)
        self.statusBar().setStyleSheet("QStatusBar { background:#e9eef6; color:#667993; padding:3px 12px; }")
        self.statusBar().showMessage("先断开手机蓝牙助手，再扫描连接 CH9141。")
        self.notify_uuid, self.write_uuid = NOTIFY_UUID, WRITE_UUID

    def build_scope(self):
        page = W.QWidget()
        layout = W.QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        metrics = W.QHBoxLayout()
        self.metrics = {}
        for title, key, note in [("接收速率", "rate", "数据包 / 秒"), ("本段样本", "count", "完整帧持续记录"),
                                 ("接收间隔", "gap", "最近窗口最大间隔"), ("解析异常", "errors", "保留异常证据")]:
            frame, box = card()
            box.addWidget(label(title, "muted"))
            value = label("—", "metric")
            self.metrics[key] = value
            box.addWidget(value)
            box.addWidget(label(note, "muted"))
            metrics.addWidget(frame)
        layout.addLayout(metrics)
        frame, box = card()
        titlebar = W.QHBoxLayout()
        titlebar.addWidget(label("实时信号", "section"))
        titlebar.addStretch()
        self.pause_button = button("Ⅱ  暂停", self.pause)
        titlebar.addWidget(self.pause_button)
        self.follow = W.QCheckBox("跟随最新")
        self.follow.setChecked(True)
        titlebar.addWidget(self.follow)
        self.auto_y = W.QCheckBox("自动量程")
        self.auto_y.setChecked(True)
        titlebar.addWidget(self.auto_y)
        titlebar.addWidget(button("复位视图", self.reset_view))
        box.addLayout(titlebar)
        controls = W.QHBoxLayout()
        controls.addWidget(label("横轴", "muted"))
        self.axis_mode = W.QComboBox()
        self.axis_mode.addItems(["样本顺序", "真实接收时间", "设备采样时间"])
        self.axis_mode.currentIndexChanged.connect(self.reset_view)
        controls.addWidget(self.axis_mode)
        controls.addSpacing(12)
        controls.addWidget(label("显示窗口", "muted"))
        self.span = W.QComboBox()
        self.span.addItems(["200", "500", "1000", "2000"])
        self.span.setCurrentText("200")
        self.span.currentTextChanged.connect(self.reset_view)
        controls.addWidget(self.span)
        controls.addStretch()
        self.axis_note = label("按样本排列；不代表实际采样时间。", "muted")
        controls.addWidget(self.axis_note)
        box.addLayout(controls)
        pg.setConfigOptions(antialias=True, background="w", foreground="#8190a4", useOpenGL=False)
        self.plot = pg.PlotWidget()
        self.plot.setMinimumHeight(260)
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=True, y=True, alpha=0.13)
        self.plot.getPlotItem().hideButtons()
        self.plot.getViewBox().setDefaultPadding(0.03)
        self.plot.getViewBox().setAutoVisible(y=True)
        self.plot.setLabel("left", "数值 / 原始单位")
        self.plot.setLabel("bottom", "样本序号")
        self.plot.getAxis("left").setWidth(65)
        for name in ("left", "bottom"):
            self.plot.getAxis(name).setPen(pg.mkPen("#dbe3ef"))
            self.plot.getAxis(name).setTextPen(pg.mkPen("#91a0b3"))
            self.plot.getAxis(name).setTickFont(G.QFont("Consolas", 9))
        self.plot.getViewBox().sigRangeChangedManually.connect(lambda *_: self.follow.setChecked(False))
        self.crosshair = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#b4c1d4", style=C.Qt.PenStyle.DashLine))
        self.plot.addItem(self.crosshair, ignoreBounds=True)
        self.crosshair.hide()
        self.hover_text = label("移动鼠标查看采样值 · 滚轮缩放 · 拖动平移", "muted")
        self.plot.scene().sigMouseMoved.connect(self.hover)
        box.addWidget(self.plot, 1)
        box.addWidget(self.hover_text)
        self.channel_layout = W.QHBoxLayout()
        self.channel_layout.addStretch()
        box.addLayout(self.channel_layout)
        layout.addWidget(frame, 1)
        self.quality_note = label("接收时间可能受 BLE 批量传输影响。波形不做视觉滤波，原始数值保持不变。", "notice")
        self.quality_note.setWordWrap(True)
        layout.addWidget(self.quality_note)
        return page

    def build_parameters(self):
        page, layout = card()
        layout.addWidget(label("设备参数", "section"))
        hint = label("参数请求与设备确认分开显示。只有实际 ACK 才标记生效；旧版固件需先接入 DebugLink。", "notice")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        row = W.QHBoxLayout()
        self.param_key = W.QComboBox()
        self.param_key.setEditable(True)
        self.param_key.addItems(["filter.alpha", "pid.kp", "pid.ki", "pid.kd", "roll.kp", "roll.ki", "roll.kd", "pitch.kp", "pitch.ki", "pitch.kd"])
        self.param_value = W.QDoubleSpinBox()
        self.param_value.setRange(-100000, 100000)
        self.param_value.setDecimals(6)
        self.param_value.setValue(0.02)
        row.addWidget(self.param_key, 2)
        row.addWidget(self.param_value, 1)
        row.addWidget(button("发送参数", self.send_param, True))
        row.addWidget(button("读取设备参数", self.get_params))
        layout.addLayout(row)
        self.param_table = W.QTableWidget(0, 4)
        self.param_table.setHorizontalHeaderLabels(["参数名", "数值", "状态", "来源"])
        self.param_table.horizontalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Stretch)
        self.param_table.verticalHeader().hide()
        self.param_table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.param_table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.param_table.itemSelectionChanged.connect(self.choose_param)
        layout.addWidget(self.param_table, 1)
        layout.addWidget(label("未提供 PID 自动整定；界面参数名不是推荐增益，范围和控制对象由固件定义。", "muted"))
        return page

    def build_filters(self):
        page, layout = card()
        layout.addWidget(label("在电脑上验证滤波，再决定是否修改固件", "section"))
        notice = label("本地实验结果单独显示为 local.preview，不下发设备，也不会修改原始记录。", "notice")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        form = W.QFormLayout()
        form.setVerticalSpacing(18)
        self.filter_kind = W.QComboBox()
        self.filter_kind.addItems(["关闭", "一阶低通 EMA", "滑动均值", "中值滤波", "RC低通", "互补滤波"])
        self.filter_input = W.QLineEdit("roll_acc")
        self.filter_gyro = W.QLineEdit("gyro_y")
        self.filter_alpha = W.QDoubleSpinBox()
        self.filter_alpha.setRange(0, 1)
        self.filter_alpha.setDecimals(4)
        self.filter_alpha.setSingleStep(0.01)
        self.filter_alpha.setValue(0.1)
        self.filter_window = W.QSpinBox()
        self.filter_window.setRange(1, 101)
        self.filter_window.setValue(5)
        self.filter_cutoff = W.QDoubleSpinBox()
        self.filter_cutoff.setRange(0.01, 1000)
        self.filter_cutoff.setValue(10)
        for title, widget in [("滤波器", self.filter_kind), ("输入通道", self.filter_input), ("角速度通道（°/s）", self.filter_gyro),
                               ("修正 / 平滑系数 alpha", self.filter_alpha), ("窗口点数", self.filter_window), ("截止频率 Hz", self.filter_cutoff)]:
            form.addRow(title, widget)
        layout.addLayout(form)
        layout.addWidget(button("应用本地预览 → 返回波形", self.apply_filter, True))
        detail = label("互补滤波需输入角度与同轴角速度，方向必须一致。\nRC和互补滤波优先使用设备时间戳；旧版plot只有接收时间，成批到达时本地计算不能代表MCU滤波效果。", "muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)
        layout.addStretch()
        return page

    def build_logs(self):
        page, layout = card()
        layout.addWidget(label("会话记录与协议诊断", "section"))
        row = W.QHBoxLayout()
        row.addWidget(button("回放 JSONL", self.replay))
        row.addWidget(button("打开当前会话", lambda: os.startfile(self.session.folder)))
        row.addWidget(button("导出 agent 分析包", self.export, True))
        row.addStretch()
        layout.addLayout(row)
        self.mapping = W.QLineEdit("roll_acc,roll_est")
        mapping_row = W.QHBoxLayout()
        mapping_row.addWidget(label("plot通道名", "muted"))
        mapping_row.addWidget(self.mapping)
        mapping_row.addWidget(button("应用名称", self.set_mapping))
        layout.addLayout(mapping_row)
        self.logs = W.QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(250)
        layout.addWidget(self.logs, 1)
        self.path_label = label(str(self.session.folder), "muted")
        self.path_label.setTextInteractionFlags(C.Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)
        return page

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, item in enumerate(self.nav):
            item.setChecked(i == index)
        titles = ["信号工作台", "参数调试", "滤波实验室", "会话记录"]
        self.page_title.setText(titles[index])
        self.page_description.setText(["捕捉每一次变化，让调参有据可依。", "每次修改都有请求、确认和记录。", "比较原始信号与本地算法输出。", "让曲线、参数和判断依据一起留存。 "][index])

    def scan(self):
        if self.scanning or self.connecting:
            return
        self.scanning = True
        self.scan_started = time.monotonic()
        self.scan_button.setEnabled(False)
        self.connect_button.setEnabled(False)
        self.scan_button.setText("正在扫描…")
        self.scan_feedback.setText("正在查找设备，请等待约6秒…")
        self.scan_feedback.setStyleSheet("color:#8cbbff;font-size:11px;")
        self.statusBar().showMessage("正在扫描 BLE 设备…")
        future = self.transport.submit(self.transport.scan())
        future.add_done_callback(lambda _: self.transport.notices.put(("scan_done", "")))

    def populate_devices(self, devices):
        previous = self.device_combo.currentData()
        self.devices = devices
        self.device_combo.clear()
        preferred = 0
        has_ch9141 = False
        for index, device in enumerate(devices):
            name = device["name"] or "未命名设备"
            self.device_combo.addItem(f"{name}  {device['rssi']}dBm\n{device['address']}", device["address"])
            self.device_combo.setItemData(index, f"{name}\n{device['address']}\n信号 {device['rssi']} dBm", C.Qt.ItemDataRole.ToolTipRole)
            if "CH914" in name.upper():
                has_ch9141 = True
            if device["address"] == previous:
                preferred = index
        if devices:
            self.device_combo.setCurrentIndex(preferred)
            suffix = "已发现CH9141" if has_ch9141 else "请选择目标设备，确认设备已上电"
            self.scan_feedback.setText(f"扫描完成：{len(devices)} 台\n{suffix}；点击下拉框选择。")
            self.scan_feedback.setStyleSheet("color:#78d9bd;font-size:11px;")
        else:
            self.device_combo.setPlaceholderText("没有发现设备，点击重新扫描")
            self.scan_feedback.setText("没有发现设备。请确认电脑蓝牙已打开、目标设备已上电。")
            self.scan_feedback.setStyleSheet("color:#efbd7d;font-size:11px;")
        self.connect_button.setEnabled(bool(devices) and not self.scanning)

    def connect_device(self):
        if self.connecting or self.scanning:
            return
        index = self.device_combo.currentIndex()
        if index < 0 or index >= len(self.devices):
            self.statusBar().showMessage("请先扫描并选择 CH9141。")
            return
        self.statusBar().showMessage("连接中，发现 GATT 特征…")
        self.connecting = True
        self.connect_started = time.monotonic()
        self.connect_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.device_combo.setEnabled(False)
        self.connect_button.setText("正在连接…")
        self.scan_feedback.setText(f"正在连接 {self.devices[index]['name']}…")
        self.scan_feedback.setStyleSheet("color:#8cbbff;font-size:11px;")
        future = self.transport.submit(self.transport.connect(self.devices[index]["address"], self.notify_uuid, self.write_uuid))
        future.add_done_callback(lambda _: self.transport.notices.put(("connect_done", "")))

    def advanced(self):
        dialog = W.QDialog(self)
        dialog.setWindowTitle("CH9141 高级连接设置")
        form = W.QFormLayout(dialog)
        notify = W.QLineEdit(self.notify_uuid)
        write = W.QLineEdit(self.write_uuid)
        notify.setMinimumWidth(360)
        form.addRow("通知 UUID", notify)
        form.addRow("透传写 UUID", write)
        form.addRow(label("FFF3 是模块配置通道，不用于本软件的参数透传。", "muted"))
        apply = button("保存", dialog.accept, True)
        form.addRow(apply)
        if dialog.exec():
            self.notify_uuid, self.write_uuid = notify.text().strip(), write.text().strip()

    def reset_view(self, *_):
        items = ["200", "500", "1000", "2000"] if self.axis_mode.currentIndex() == 0 else ["5", "10", "20", "60"]
        if self.span.itemText(0) != items[0]:
            self.span.blockSignals(True)
            self.span.clear()
            self.span.addItems(items)
            self.span.blockSignals(False)
        self.last_signature = None
        self.display_end = None
        self.follow.setChecked(True)
        self.plot.enableAutoRange(axis="y", enable=self.auto_y.isChecked())

    def pause(self):
        self.paused = not self.paused
        self.frozen = self.session.copy_samples(5000) if self.paused else []
        self.pause_button.setText("▶  继续" if self.paused else "Ⅱ  暂停")
        self.last_signature = None
        self.statusBar().showMessage("画面暂停，后台仍记录" if self.paused else "已恢复实时显示")

    def send_param(self):
        try:
            seq, payload = self.session.prepare_set(self.param_key.currentText().strip(), self.param_value.value())
            self.transport.submit(self.transport.send_parameter(seq, payload, self.transport.generation))
            self.statusBar().showMessage(f"请求 #{seq} 已发送，等待设备确认。")
        except ValueError as exc:
            self.statusBar().showMessage(str(exc))

    def get_params(self):
        if not self.session.connected:
            self.statusBar().showMessage("请先连接设备。")
            return
        with self.session.lock:
            self.session.seq += 1
            seq = self.session.seq
            self.session.event("query", "读取设备参数", seq=seq)
        self.transport.submit(self.transport.send(f"[get,{seq}]".encode()))

    def choose_param(self):
        row = self.param_table.currentRow()
        if row >= 0:
            self.param_key.setCurrentText(self.param_table.item(row, 0).text())
            self.param_value.setValue(float(self.param_table.item(row, 1).text()))

    def apply_filter(self):
        config = {"kind": self.filter_kind.currentText(), "input": self.filter_input.text().strip(),
                  "gyro": self.filter_gyro.text().strip(), "alpha": self.filter_alpha.value(),
                  "window": self.filter_window.value(), "cutoff": self.filter_cutoff.value()}
        with self.session.lock:
            self.session.preview.configure(config)
            self.session.event("local_filter", "本地滤波预览", config=config)
        self.navigate(0)
        self.statusBar().showMessage("本地预览已应用；未向设备写入任何参数。")

    def set_mapping(self):
        from protocol import KEY
        names = [x.strip() for x in self.mapping.text().split(",")]
        if not 1 <= len(names) <= 32 or len(set(names)) != len(names) or not all(KEY.fullmatch(x) for x in names):
            self.statusBar().showMessage("通道名必须唯一，使用英文/数字/下划线/点，以字母开头。")
            return
        with self.session.lock:
            self.session.plot_names = names
            self.session.event("mapping", ",".join(names))
        self.statusBar().showMessage("通道名应用到后续样本。")

    def replay(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "回放会话", str(BASE / "sessions"), "JSONL (*.jsonl)")
        if path and Path(path).resolve() != (self.session.folder / "frames.jsonl").resolve():
            self.transport.submit(self.transport.replay(path))

    def export(self):
        future = self.worker.submit(self.session.report)
        def done(result):
            try:
                self.transport.notices.put(("report", str(result.result())))
            except Exception as exc:
                self.transport.notices.put(("error", str(exc)))
        future.add_done_callback(done)
        self.statusBar().showMessage("正在后台生成统计和分析报告…")

    def hover(self, point):
        if not self.plot.sceneBoundingRect().contains(point) or not self.data:
            self.crosshair.hide()
            return
        mapped = self.plot.getViewBox().mapSceneToView(point)
        x = mapped.x()
        if not hasattr(self, "plot_x") or len(self.plot_x) == 0:
            return
        index = min(int(np.searchsorted(self.plot_x, x)), len(self.data) - 1)
        self.crosshair.setPos(self.plot_x[index])
        self.crosshair.show()
        self.hover_text.setText(f"{self.plot_x[index]:.3f}  ·  " + "    ".join(f"{k}  {v:.4f}" for k, v in self.data[index]["values"].items()))

    def update_curves(self, now):
        if self.stack.currentIndex() != 0:
            return
        with self.session.lock:
            last = self.session.samples[-1]["t"] if self.session.samples else None
        signature = (last, self.axis_mode.currentIndex(), self.span.currentText(), self.paused,
                     tuple((k, checkbox.isChecked()) for k, checkbox in self.checks.items()))
        if signature != self.last_signature:
            started = time.perf_counter()
            self.data = self.frozen if self.paused else self.session.copy_samples(5000)
            if self.data:
                mode = self.axis_mode.currentIndex()
                self.plot_x, actual_axis = x_coordinates(self.data, mode)
                if mode == 0:
                    self.plot.setLabel("bottom", "样本序号（不代表采样时间）")
                    self.axis_note.setText("样本等距展示，原始值不变；时间间断请切换接收时间。")
                elif actual_axis == "device_s":
                    self.plot.setLabel("bottom", "设备时间 / s")
                    self.axis_note.setText("使用固件上报的设备时间。")
                else:
                    self.plot.setLabel("bottom", "真实接收时间 / s")
                    self.axis_note.setText("接收时间包含BLE分包、聚包和传输间隔。" if mode == 1 else "固件没有时间戳，已回退到真实接收时间。")
                keys = list(dict.fromkeys(k for row in self.data[-100:] for k in row["values"]))
                for key in keys:
                    if key not in self.curves and len(self.curves) < 32:
                        color = COLORS[len(self.curves) % len(COLORS)]
                        curve = self.plot.plot(pen=pg.mkPen(color, width=1.6), antialias=True, connect="finite")
                        curve.setClipToView(True)
                        curve.setDownsampling(auto=True, method="peak")
                        self.curves[key] = curve
                        check = W.QCheckBox(key)
                        check.setChecked(len(self.checks) < 6)
                        check.setStyleSheet(f"QCheckBox {{ color:{color}; font-weight:600; }}")
                        check.toggled.connect(lambda _: setattr(self, "last_signature", None))
                        self.checks[key] = check
                        self.channel_layout.insertWidget(self.channel_layout.count() - 1, check)
                for key, curve in self.curves.items():
                    if key in keys and self.checks[key].isChecked():
                        y = np.array([row["values"].get(key, np.nan) for row in self.data], dtype=float)
                        curve.setData(self.plot_x, y)
                        curve.show()
                    else:
                        curve.hide()
                self.target_end = float(self.plot_x[-1])
                if self.display_end is None or self.target_end < self.display_end:
                    self.display_end = self.target_end
            else:
                for curve in self.curves.values():
                    curve.hide()
            self.last_signature = signature
            self.last_plot_ms = (time.perf_counter() - started) * 1000
        if self.follow.isChecked() and self.data and self.target_end is not None:
            # Ease only the viewport, never the measured values.
            dt = min(now - self.last_frame_time, 0.1)
            difference = self.target_end - self.display_end
            if self.paused or abs(difference) < 0.005:
                self.display_end = self.target_end
            else:
                self.display_end += difference * (1 - np.exp(-dt / 0.065))
            width = float(self.span.currentText())
            end = self.display_end
            self.plot.setXRange(max(0, end - width), max(width, end), padding=0, update=False)
        self.plot.enableAutoRange(axis="y", enable=self.auto_y.isChecked())

    def refresh(self):
        if self.closed:
            return
        now = time.monotonic()
        try:
            for _ in range(30):
                try:
                    kind, value = self.transport.notices.get_nowait()
                except queue.Empty:
                    break
                if kind == "devices":
                    self.populate_devices(value)
                    self.statusBar().showMessage(f"发现 {len(value)} 个BLE设备，选择CH9141连接。")
                elif kind == "scan_done":
                    self.scanning = False
                    self.scan_button.setEnabled(True)
                    self.scan_button.setText("重新扫描蓝牙设备")
                    self.connect_button.setEnabled(self.device_combo.currentIndex() >= 0)
                elif kind == "connect_done":
                    self.connecting = False
                    self.scan_button.setEnabled(True)
                    self.device_combo.setEnabled(True)
                    self.connect_button.setText("连接选中设备")
                    self.connect_button.setEnabled(self.device_combo.currentIndex() >= 0)
                    if self.session.connected:
                        self.scan_feedback.setText("设备已连接，等待或接收遥测数据。")
                        self.scan_feedback.setStyleSheet("color:#78d9bd;font-size:11px;")
                elif kind == "report":
                    W.QApplication.clipboard().setText(value)
                    self.statusBar().showMessage(f"分析包路径已复制：{value}")
                else:
                    if kind == "error" and (self.scanning or self.connecting):
                        self.scan_feedback.setText(("扫描失败：" if self.scanning else "连接失败：") + str(value))
                        self.scan_feedback.setStyleSheet("color:#efbd7d;font-size:11px;")
                    self.statusBar().showMessage(str(value))
            if self.scanning:
                elapsed = int(now - self.scan_started)
                self.scan_button.setText(f"正在扫描… {elapsed}s")
            if self.connecting:
                self.connect_button.setText(f"正在连接… {int(now - self.connect_started)}s")
            self.update_curves(now)
            self.frames += 1
            if now - self.fps_started >= 1:
                self.fps = self.frames / (now - self.fps_started)
                self.frames = 0
                self.fps_started = now
                self.fps_label.setText(f"绘图更新 {self.last_plot_ms:.1f} ms   /   UI {self.fps:.0f} Hz")
            if now - self.last_status >= 0.25:
                self.last_status = now
                snapshot = self.session.snapshot()
                self.snapshot_cache = snapshot
                source = snapshot["source"]
                self.badge.setText({"ble": "●  真机在线", "demo": "●  模拟数据", "replay": "●  离线回放", "offline": "○  离线"}[source])
                self.connection_text.setText(snapshot["device"])
                self.metrics["rate"].setText(f"{snapshot['receive_rate_hz']:.1f} Hz")
                with self.session.lock:
                    recent = list(self.session.arrivals)[-300:]
                    count = len(self.session.samples)
                gaps = np.diff(recent) if len(recent) > 1 else []
                gap = float(max(gaps)) * 1000 if len(gaps) else 0
                self.metrics["gap"].setText(f"{gap:.0f} ms")
                self.metrics["count"].setText(f"{count:,}")
                self.metrics["errors"].setText(str(snapshot["invalid_frames"] + snapshot["discarded_frames"]))
                age = snapshot["last_rx_age_s"]
                if age is not None and age > 1 and source == "ble":
                    self.quality_note.setText(f"已 {age:.1f} 秒未收到新数据。界面仍响应；这是遥测间断，不会补造测量值。")
                else:
                    self.quality_note.setText("样本顺序模式避免BLE聚包产生的竖线；查看真实时序请切换横轴。原始记录保持真实接收时间。")
                if snapshot["parameters"] != self.param_signature:
                    self.param_signature = {k: dict(v) for k, v in snapshot["parameters"].items()}
                    self.param_table.setRowCount(len(self.param_signature))
                    for i, (key, item) in enumerate(self.param_signature.items()):
                        for col, text in enumerate([key, f"{item['value']:.8g}", STATUS.get(item["status"], item["status"]), item.get("source", source)]):
                            self.param_table.setItem(i, col, W.QTableWidgetItem(text))
                if self.stack.currentIndex() == 3:
                    with self.session.lock:
                        events = list(self.session.events)[-40:]
                        raw = list(self.session.raw)[-30:]
                    text = "\n".join(f"{x['utc'][11:19]}  {x['type']}  {x['message']}" for x in events)
                    text += "\n\n--- RX / TX ---\n" + "\n".join(f"{direction}  {frame}" for direction, frame in raw)
                    if text != self.log_signature:
                        self.logs.setPlainText(text)
                        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())
                        self.log_signature = text
            self.last_frame_time = now
        except Exception as exc:
            self.statusBar().showMessage(f"界面更新异常：{exc}")

    def closeEvent(self, event):
        self.closed = True
        self.timer.stop()
        self.transport.close()
        self.worker.shutdown(wait=True, cancel_futures=True)
        if self.api:
            self.api.shutdown()
            self.api.server_close()
        self.session.close()
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    app = W.QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    app.setFont(G.QFont("Microsoft YaHei UI", 10))
    window = BLEScope(demo=args.demo or args.self_check)
    window.show()
    if args.self_check:
        def finish():
            result = {"ok": False}
            try:
                snapshot = window.session.snapshot()
                assert snapshot["source"] == "demo" and snapshot["sample_count"] > 10
                assert len(window.curves) >= 2
                assert len(window.curves["roll_acc"].getData()[0]) > 10
                window.apply_filter()
                window.navigate(1)
                window.navigate(0)
                result = {"ok": True, "samples": snapshot["sample_count"], "curves": len(window.curves),
                          "ui_hz": window.fps, "last_curve_update_ms": window.last_plot_ms}
            except Exception as exc:
                result["error"] = str(exc)
            (BASE / "qt_self_check.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
            window.close()
        C.QTimer.singleShot(3000, finish)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

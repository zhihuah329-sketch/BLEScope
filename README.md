<div align="center">
  <img src="assets/blescope.svg" width="80" alt="BLEScope logo" />
  <h1>BLEScope</h1>
  <p><b>BLE 遥测、实时波形、参数调试与滤波实验工作台</b></p>
  <p>Windows · Qt / pyqtgraph · 本地记录 · Agent 可读</p>
</div>

BLEScope 是一个 Windows 桌面 BLE 调试工具，使用 Qt 和 pyqtgraph 绘制实时曲线，
通过 Bleak 连接 BLE GATT 设备。当前默认适配 **CH9141 BLE 串口透传**，也可以配置其他
BLE 透传模块的通知和写入 UUID。

它不是通用蓝牙设备管理器：经典蓝牙 SPP、蓝牙音频、HID 以及任意厂商二进制协议，
目前不直接支持。其他 BLE 设备需要匹配 GATT 特征和数据协议。

## 功能

- **实时波形**：缩放、平移、暂停、通道选择、自动量程、跟随最新。
- **三种横轴**：样本顺序、真实接收时间、设备采样时间；避免把 BLE 聚包误认为信号跳变。
- **参数调试**：请求序号、设备 ACK、回读、拒绝和超时状态；不会把发送完成冒充参数生效。
- **本地滤波实验**：一阶低通、滑动均值、中值、RC 低通和互补滤波。
- **会话记录**：JSONL 原始帧、CSV 数值、JSON 快照和 Markdown 分析报告。
- **Agent 接口**：只读 localhost API 和结构化文件，方便结合嵌入式源码分析数据。
- **无硬件演示 / 日志回放**：明确标注模拟与回放来源。

## 快速开始

### Windows 可执行文件

从仓库 **Releases** 下载 Windows 压缩包，解压后运行 `BLEScope.exe`。
程序会在同目录创建 `sessions/`，请放在可写目录中。

1. 打开电脑蓝牙，给目标设备上电；若手机占用了连接，先断开手机。
2. 点击“扫描蓝牙设备”，查看计时和扫描结果。
3. 点击设备下拉框，按名称、信号和地址选择设备，再点击连接。
4. 设备发送支持的遥测格式后，曲线自动出现。

CH9141 默认特征为：

| 项目 | UUID |
|---|---|
| 透传服务 | `0000fff0-0000-1000-8000-00805f9b34fb` |
| 接收通知 | `0000fff1-0000-1000-8000-00805f9b34fb` |
| 透传写入 | `0000fff2-0000-1000-8000-00805f9b34fb` |

通过“高级 UUID 设置”可修改通知与写入特征。FFF3 是 CH9141 模块配置通道，
本软件不使用它修改模块波特率或其他模块设置。

### 从源码运行

建议 Python 3.12，Windows 10/11 64 位：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python qt_app.py
```

不连接硬件查看界面：

```powershell
python qt_app.py --demo
```

## 遥测与参数协议

兼容已有的简单绘图帧：

```text
[plot,3.25,3.18]
```

默认通道名为 `roll_acc,roll_est`，可以在“会话与日志”里修改映射。
这只是通道名称，不会推断数据是否已经融合、量程和单位。

推荐命名遥测，第二字段是 MCU 毫秒时间戳：

```text
[data,1250,roll_acc=3.25,roll_est=3.18,gyro_y=0.5,target=0,output=1.2]
```

调参需要设备固件实现命令处理。仅能发送遥测的旧固件不能直接调参：

```text
电脑 → [set,1,filter.alpha,0.02]
设备 → [ack,1,filter.alpha,0.02]

电脑 → [get,2]
设备 → [param,2,filter.alpha,0.02]

设备拒绝 → [nack,1,out_of_range]
```

格式、单位与固件集成见 [协议说明](docs/PROTOCOL.md) 和
[STM32 接入示例](docs/FIRMWARE.md)。设备可修改参数的范围由固件定义，
本软件不内置 PID 推荐增益或自动整定。

## 波形时间轴

| 模式 | 用途 | 注意 |
|---|---|---|
| 样本顺序 | 连续比较数值、避免聚包挤成竖线 | 样本等距展示，不代表真实采样间隔 |
| 真实接收时间 | 检查蓝牙成批到达、停顿与抖动 | 是电脑接收时间，不等于 MCU 采样时间 |
| 设备采样时间 | 检查固件上报时间轴 | 需要每帧都有有效 `device_ms`，缺失时明确回退 |

滚动动画只调整视口，不插造测量值，不以视觉平滑掩盖尖峰。
顶部“接收速率”是主机包速率，不是 IMU 的内部 ODR。

## 本地滤波实验

选择输入通道和滤波器后，结果显示为 `local.preview`。
它只在电脑计算，不会下发到设备；配置和派生数据一并记录。
互补滤波输入需为角度与同轴角速度，单位分别为 ° 和 °/s。
没有设备时间戳时，基于接收间隔计算的滤波预览不能证明 MCU 的实时滤波效果。

## 让 Agent 帮助分析

点击“导出分析包”，报告路径会复制到剪贴板。会话目录包含：

```text
sessions/<session-id>/
├── frames.jsonl      # 原始帧、遥测与参数事件
├── samples.csv       # 通道长表
├── snapshot.json     # 每秒更新的当前状态
├── analysis.json     # 通道统计和主机接收间隔
└── agent_report.md   # 可直接提供给 Agent 的分析报告
```

只读 API 监听 `127.0.0.1:8767`：

```text
GET /snapshot
GET /samples?limit=1000
GET /events
GET /health
```

接口不提供远程参数写入。Agent 的建议应结合源码、轴向、量程、实际 dt 和运行工况；
斜率不能直接视为零偏，未检测测试工况时不能自动推导 PID 增益。

## 开发与构建

```powershell
python -m unittest discover -v
python qt_app.py --self-check

python -m pip install -r requirements-build.txt
python build_release.py
```

输出在 `dist/`，便携发布包在 `release/`。
构建使用 PyInstaller 和固定 Qt 绑定，规避开发机 PATH 中同名 DLL 干扰。

CI 运行无硬件测试和模拟 GUI 自检。真实 BLE 测试需要蓝牙适配器与目标设备。
调试记录、本机路径、构建目录和可执行文件不会提交到源码仓库。

## 项目结构

```text
qt_app.py        Qt 界面与绘图调度
transport.py     后台 BLE、演示与回放
protocol.py      分包/粘包帧解析与数据校验
session.py       会话、统计、文件记录和只读 API
filters.py       本地滤波预览
qt_waveform.py   横轴转换
firmware/       STM32 参数协议接入代码
docs/           协议与固件说明
```

## 许可与参考

本项目源码采用 [MIT License](LICENSE)。依赖保留各自许可，详见
[第三方声明](THIRD_PARTY_NOTICES.md)。BLEScope 是独立项目，不是 WCH 官方软件。

- [WCH CH9141 官方资料](https://github.com/openwch/ch9141)
- [Bleak 文档](https://bleak.readthedocs.io/)
- [pyqtgraph 文档](https://pyqtgraph.readthedocs.io/)
- [Qt for Python](https://doc.qt.io/qtforpython-6/)

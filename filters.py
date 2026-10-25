"""Local previews only. They never change MCU parameters."""
import math
import statistics
from collections import deque


class FilterPreview:
    def __init__(self):
        self.config = {"kind": "关闭", "input": "roll_acc", "gyro": "gyro_y",
                       "alpha": 0.1, "window": 5, "cutoff": 10.0}
        self.reset()

    def reset(self):
        self.value = None
        self.last_t = None
        self.history = deque(maxlen=101)

    def configure(self, config):
        if config["kind"] not in ("关闭", "一阶低通 EMA", "滑动均值", "中值滤波", "RC低通", "互补滤波"):
            raise ValueError("未知滤波器")
        if not 0 <= float(config["alpha"]) <= 1:
            raise ValueError("alpha 必须在0..1之间")
        if not 1 <= int(config["window"]) <= 101:
            raise ValueError("窗口必须在1..101之间")
        if not math.isfinite(float(config["cutoff"])) or not 0 < float(config["cutoff"]) <= 1000:
            raise ValueError("截止频率必须在0..1000Hz之间")
        self.config = dict(config)
        self.reset()

    def update(self, values, elapsed, device_ms):
        cfg = self.config
        if cfg["kind"] == "关闭" or cfg["input"] not in values:
            return None
        value = values[cfg["input"]]
        stamp = device_ms / 1000 if device_ms is not None else elapsed
        dt = None if self.last_t is None else stamp - self.last_t
        self.last_t = stamp
        if self.value is None or (dt is not None and not 0 < dt < 1):
            self.value = value
            self.history.clear()
        self.history.append(value)
        alpha = float(cfg["alpha"])
        kind = cfg["kind"]
        if kind == "一阶低通 EMA":
            self.value += alpha * (value - self.value)
        elif kind in ("滑动均值", "中值滤波"):
            window = list(self.history)[-int(cfg["window"]):]
            self.value = statistics.mean(window) if kind == "滑动均值" else statistics.median(window)
        elif kind == "RC低通" and dt and 0 < dt < 1:
            tau = 1 / (2 * math.pi * float(cfg["cutoff"]))
            self.value += dt / (tau + dt) * (value - self.value)
        elif kind == "互补滤波":
            if cfg["gyro"] not in values:
                return None
            if dt and 0 < dt < 1:
                self.value += values[cfg["gyro"]] * dt
                error = (value - self.value + 180) % 360 - 180
                self.value = (self.value + alpha * error + 180) % 360 - 180
        return self.value

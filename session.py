from __future__ import annotations

import csv
import json
import math
import statistics
import os
import threading
import time
import uuid
from collections import deque
from itertools import islice
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from protocol import FrameParser, decode, set_command
from filters import FilterPreview


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self, root: Path):
        self.lock = threading.RLock()
        self.parser = FrameParser()
        self.preview = FilterPreview()
        self.plot_names = ["roll_acc", "roll_est"]
        self.started = time.monotonic()
        self.arrivals = deque(maxlen=10000)
        self.tick_lock = threading.Lock()
        self.maintenance_stop = threading.Event()
        self.maintenance = None
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
        self.folder = root / self.id
        self.folder.mkdir(parents=True)
        self.frames = (self.folder / "frames.jsonl").open("a", encoding="utf-8", buffering=65536)
        self.csv_file = (self.folder / "samples.csv").open("a", encoding="utf-8", newline="")
        self.writer = csv.writer(self.csv_file)
        self.writer.writerow(["host_utc", "elapsed_s", "source", "device_ms", "channel", "value"])
        self.samples = deque(maxlen=30000)
        self.events = deque(maxlen=300)
        self.raw = deque(maxlen=300)
        self.params = {}
        self.pending = {}
        self.seq = 0
        self.source = "offline"
        self.data_source = "none"
        self.connected = False
        self.device = "未连接"
        self.total = 0
        self.invalid = 0
        self.last_rx = None
        self.latest = {}
        self.latest_times = {}
        self.closed = False
        self.event("session", "新会话", session=self.id)

    def event(self, kind, message, **extra):
        with self.lock:
            if self.closed:
                return
            event = {"type": kind, "utc": utc_now(), "message": message, **extra}
            self.events.append(event)
            self.frames.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")

    def link(self, source, connected, device):
        with self.lock:
            self.parser = FrameParser()
            self.source, self.connected, self.device = source, connected, device
            # Keep the last capture on disconnect so it can still be exported.
            if source != "offline":
                self.preview.reset()
                self.data_source = source
                self.samples.clear()
                self.latest.clear()
                self.latest_times.clear()
                self.last_rx = None
                self.arrivals.clear()
                self.params.clear()
            else:
                for request in self.pending.values():
                    self.params[request["key"]] = {"value": request["value"], "status": "disconnected_unknown"}
            self.pending.clear()
            self.event("link", device, source=source, connected=connected)

    def ingest(self, data: bytes):
        with self.lock:
            if self.closed:
                return
            for frame in self.parser.feed(data):
                self.raw.append(("RX", frame))
                try:
                    item = decode(frame, self.plot_names)
                except (ValueError, OverflowError) as exc:
                    self.invalid += 1
                    self.event("parse_error", str(exc), frame=frame)
                    continue
                stamp = time.monotonic()
                record = {**item, "utc": utc_now(), "t": stamp - self.started,
                          "source": self.source, "frame": frame}
                if item["type"] == "sample":
                    record["sample_index"] = self.total
                    preview = self.preview.update(item["values"], record["t"], item["device_ms"])
                    if preview is not None:
                        item["values"]["local.preview"] = preview
                    record["preview_config"] = dict(self.preview.config)
                self.frames.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
                if item["type"] == "sample":
                    self.samples.append(record)
                    self.latest.update(item["values"])
                    for key in item["values"]:
                        self.latest_times[key] = record["t"]
                    self.total += 1
                    self.last_rx = stamp
                    self.arrivals.append(stamp)
                    for key, value in item["values"].items():
                        self.writer.writerow([record["utc"], f"{record['t']:.6f}", self.source,
                                              item["device_ms"], key, value])
                elif item["type"] in ("ack", "param"):
                    pending = self.pending.get(item["seq"])
                    if item["type"] == "ack":
                        if not pending or pending["key"] != item["key"]:
                            self.event("unmatched_ack", "忽略未匹配的确认", response=item)
                            continue
                        del self.pending[item["seq"]]
                    elif any(p["key"] == item["key"] for p in self.pending.values()):
                        # A get response must not masquerade as the ACK of a set.
                        self.params[item["key"]]["last_readback"] = item["value"]
                        self.event("param", f"{item['key']} 回读 {item['value']}，写请求仍待确认")
                        continue
                    self.params[item["key"]] = {"value": item["value"], "status": "confirmed",
                                                "source": self.source, "utc": record["utc"]}
                    self.event(item["type"], f"{item['key']} = {item['value']}（设备回读）")
                elif item["type"] == "nack":
                    pending = self.pending.pop(item["seq"], None)
                    if pending:
                        self.params[pending["key"]] = {"value": pending["value"], "status": "rejected"}
                    self.event("rejected", item["reason"])

    def prepare_set(self, key, value):
        with self.lock:
            if not self.connected or self.source not in ("ble", "demo"):
                raise ValueError("请先连接设备；离线回放不能下发参数")
            if any(p["key"] == key for p in self.pending.values()):
                raise ValueError("该参数仍在等待设备确认")
            self.seq += 1
            payload = set_command(self.seq, key, value)
            self.pending[self.seq] = {"key": key, "value": float(value), "sent": time.monotonic()}
            self.params[key] = {"value": float(value), "status": "pending", "source": self.source}
            self.raw.append(("TX", payload.decode()))
            self.event("parameter_request", key, seq=self.seq, requested=float(value))
            return self.seq, payload

    def failed_set(self, seq, error):
        with self.lock:
            pending = self.pending.pop(seq, None)
            if pending:
                self.params[pending["key"]] = {"value": pending["value"], "status": "send_failed"}
                self.event("send_failed", str(error), seq=seq)

    def tick(self):
        with self.tick_lock:
            self._tick()

    def _tick(self):
        with self.lock:
            for seq, item in list(self.pending.items()):
                if time.monotonic() - item["sent"] > 5:
                    self.params[item["key"]] = {"value": item["value"], "status": "timeout"}
                    del self.pending[seq]
                    self.event("timeout", f"{item['key']} 未获确认；不能判断是否生效", seq=seq)
            self.csv_file.flush()
            self.frames.flush()
            snapshot = self.snapshot()
        tmp = self.folder / "snapshot.tmp"
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(tmp, self.folder / "snapshot.json")

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            while self.arrivals and now - self.arrivals[0] > 5:
                self.arrivals.popleft()
            rate = (len(self.arrivals) - 1) / (self.arrivals[-1] - self.arrivals[0]) if len(self.arrivals) > 1 and self.arrivals[-1] > self.arrivals[0] else 0
            return {"schema": "blescope/1", "session": self.id, "utc": utc_now(),
                    "source": self.source, "connected": self.connected, "device": self.device,
                    "data_source": self.data_source,
                    "sample_count": self.total, "receive_rate_hz": round(rate, 2),
                    "last_rx_age_s": None if self.last_rx is None else round(time.monotonic() - self.last_rx, 3),
                    "invalid_frames": self.invalid, "discarded_frames": self.parser.discarded,
                    "latest": dict(self.latest), "channel_elapsed_s": dict(self.latest_times),
                    "parameters": dict(self.params), "files": str(self.folder),
                    "local_filter_preview": dict(self.preview.config),
                    "note": "receive_rate_hz is host packet rate, not IMU sampling rate; demo/replay are not hardware measurements"}

    def copy_samples(self, limit=5000):
        with self.lock:
            return list(reversed(list(islice(reversed(self.samples), limit))))

    def chart_samples(self, span):
        with self.lock:
            if not self.samples:
                return []
            cutoff = self.samples[-1]["t"] - span
            rows = []
            for row in reversed(self.samples):
                if row["t"] < cutoff:
                    break
                rows.append(row)
            rows.reverse()
            return rows

    def start_maintenance(self):
        if self.maintenance:
            return
        def run():
            while not self.maintenance_stop.wait(1):
                try:
                    self.tick()
                except OSError as exc:
                    self.event("storage_error", str(exc))
        self.maintenance = threading.Thread(target=run, name="session snapshot writer", daemon=True)
        self.maintenance.start()

    def report(self):
        with self.lock:
            samples = self.copy_samples()
            snapshot = self.snapshot()
        # Statistics and file writes must not hold the telemetry/UI lock.
        if samples:
            samples = [x for x in samples if x["t"] >= samples[-1]["t"] - 30]
        gaps = [(b["t"] - a["t"]) * 1000 for a, b in zip(samples, samples[1:])]
        timing = {"host_gap_median_ms": statistics.median(gaps) if gaps else None,
                  "host_gap_max_ms": max(gaps) if gaps else None,
                  "host_gaps_over_100ms": sum(gap > 100 for gap in gaps),
                  "host_gaps_under_1ms": sum(gap < 1 for gap in gaps),
                  "note": "Sub-millisecond pairs may be frames received in the same BLE notification; host gaps do not prove MCU sample gaps."}
        channels = sorted({key for row in samples for key in row["values"]})
        lines = ["# 调试分析包", "", f"会话：{self.id}", f"数据来源：{snapshot['data_source']}",
                     "", "以下为最近最多30秒的描述统计；接收时间不等于MCU采样时间。",
                     "未检测静止/阶跃条件，不能把斜率直接判断为零偏，也不能据此自动给出PID增益。", "",
                     "| 通道 | 点数 | 最小 | 最大 | 均值 | 标准差 | 线性斜率/秒 |",
                     "|---|---:|---:|---:|---:|---:|---:|"]
        stats = {}
        for key in channels:
            points = [(x["t"], x["values"][key]) for x in samples if key in x["values"]]
            values = [v for _, v in points]
            avg = sum(values) / len(values)
            std = math.sqrt(sum((v - avg) ** 2 for v in values) / len(values))
            mt = sum(t for t, _ in points) / len(points)
            denom = sum((t - mt) ** 2 for t, _ in points)
            slope = sum((t - mt) * (v - avg) for t, v in points) / denom if denom else 0
            stats[key] = {"n": len(values), "min": min(values), "max": max(values), "mean": avg, "std": std, "slope_per_s": slope}
            lines.append(f"| {key} | {len(values)} | {min(values):.5g} | {max(values):.5g} | {avg:.5g} | {std:.5g} | {slope:.5g} |")
        lines += ["", "## 接收时序", "", "```json", json.dumps(timing, ensure_ascii=False, indent=2), "```", "",
                  "主机聚包和传输停顿可能造成时间轴竖线。需设备时间戳才能区分采样节奏与传输节奏。",
                  "", "## 当前状态与参数", "", "```json", json.dumps(snapshot, ensure_ascii=False, indent=2), "```", "",
                      "## 给 agent 的检查顺序", "", "1. 确认是真机数据，确认MCU单位、轴向、量程、dt与数据更新周期。",
                      "2. 结合原始值/滤波值/目标值/实际值/输出值，检查饱和、噪声与延迟。",
                      "3. 参数以设备确认值为准；pending/timeout/rejected不代表已生效。",
                      "4. 建议一次只修改一个参数，并说明判断依据；未经用户授权不要自动下发。"]
        (self.folder / "analysis.json").write_text(json.dumps({"source": snapshot['data_source'], "stats": stats, "timing": timing, "snapshot": snapshot}, ensure_ascii=False, indent=2), encoding="utf-8")
        path = self.folder / "agent_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.event("report", str(path))
        return path

    def close(self):
        with self.lock:
            if self.closed:
                return
        self.maintenance_stop.set()
        if self.maintenance:
            self.maintenance.join(timeout=3)
        self.tick()
        with self.lock:
            if not self.closed:
                self.closed = True
                self.frames.close()
                self.csv_file.close()


def start_api(session, port=8766):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/snapshot", "/health"):
                result = session.snapshot()
            elif parsed.path == "/samples":
                try:
                    limit = max(1, min(5000, int(parse_qs(parsed.query).get("limit", ["1000"])[0])))
                except ValueError:
                    self.send_error(400)
                    return
                result = session.copy_samples(limit)
            elif parsed.path == "/events":
                with session.lock:
                    result = list(session.events)
            else:
                self.send_error(404)
                return
            payload = json.dumps(result, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server

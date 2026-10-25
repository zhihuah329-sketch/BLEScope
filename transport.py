from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
import queue
import random
import threading
import time
from pathlib import Path

SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000fff2-0000-1000-8000-00805f9b34fb"


class Transport:
    def __init__(self, session):
        self.session = session
        self.notices = queue.Queue()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="BLE worker", daemon=True)
        self.client = None
        self.devices = {}
        self.write_char = None
        self.background = None
        self.generation = 0
        self.mode_lock = None
        self.write_lock = None
        self.thread.start()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.mode_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.loop.run_forever()
        tasks = asyncio.all_tasks(self.loop)
        for task in tasks:
            task.cancel()
        if tasks:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        self.loop.close()

    def submit(self, coro):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)

        def done(f):
            try:
                f.result()
            except (asyncio.CancelledError, concurrent.futures.CancelledError):
                pass
            except Exception as exc:
                self.session.event("transport_error", str(exc))
                message = str(exc)
                if "not powered on" in message:
                    message = "电脑蓝牙未开启，请手动打开Windows蓝牙后重新扫描"
                self.notices.put(("error", message))
        future.add_done_callback(done)
        return future

    async def scan(self):
        from bleak import BleakScanner
        async with self.mode_lock:
            if self.client or self.background:
                raise ValueError("请先断开当前连接或演示/回放，再扫描")
            found = await BleakScanner.discover(timeout=6, return_adv=True)
            self.devices = {address: device for address, (device, adv) in found.items()}
            rows = [{"address": address, "name": adv.local_name or device.name or "未命名设备",
                     "rssi": adv.rssi} for address, (device, adv) in found.items()]
            rows.sort(key=lambda row: ("CH914" not in row["name"].upper(), -row["rssi"]))
            self.notices.put(("devices", rows))
            self.session.event("scan", f"发现 {len(rows)} 个 BLE 设备")

    async def _disconnect(self):
        self.generation += 1
        if self.background:
            self.background.cancel()
            await asyncio.gather(self.background, return_exceptions=True)
            self.background = None
        client, self.client = self.client, None
        self.write_char = None
        try:
            if client:
                await asyncio.wait_for(client.disconnect(), 10)
        finally:
            self.session.link("offline", False, "已断开")
            self.notices.put(("status", "已断开"))

    async def disconnect(self):
        async with self.mode_lock:
            await self._disconnect()

    async def connect(self, address, notify_uuid, write_uuid):
        from bleak import BleakClient
        async with self.mode_lock:
            await self._disconnect()
            if address not in self.devices:
                raise ValueError("设备不在扫描结果中，请重新扫描")
            token = self.generation
            device = self.devices[address]

            def disconnected(client):
                if token == self.generation and self.client is client:
                    self.client = None
                    self.write_char = None
                    self.session.link("offline", False, "蓝牙连接已断开")
                    self.notices.put(("status", "连接中断，请手动重新扫描连接"))

            client = BleakClient(device, disconnected_callback=disconnected)
            self.client = client
            try:
                await asyncio.wait_for(client.connect(), 35)
                notify = client.services.get_characteristic(notify_uuid)
                write = client.services.get_characteristic(write_uuid)
                inventory = [{"uuid": ch.uuid, "properties": ch.properties, "service": service.uuid}
                             for service in client.services for ch in service.characteristics]
                self.session.event("gatt", "设备 GATT 特征", characteristics=inventory)
                if not notify or not ("notify" in notify.properties or "indicate" in notify.properties):
                    raise ValueError("通知特征不可用，请查看日志中的GATT列表并修改高级UUID")
                if not write or not any(p in write.properties for p in ("write", "write-without-response")):
                    raise ValueError("写特征不可用，请核对高级UUID；不要选择FFF3配置通道")
                self.write_char = write
                self.session.link("ble", True, f"{device.name or 'CH9141'} / {address}")

                def receive(_, data):
                    if token == self.generation:
                        self.session.ingest(bytes(data))

                await client.start_notify(notify, receive)
                self.notices.put(("status", "蓝牙已连接，等待遥测数据"))
            except BaseException:
                await self._disconnect()
                raise

    async def send(self, payload, generation=None):
        async with self.mode_lock:
            if generation is not None and generation != self.generation:
                raise ValueError("连接已改变，取消旧连接的参数命令")
            await self._send(payload)

    async def _send(self, payload):
        if len(payload) > 95:
            raise ValueError("单条命令最大95字节，以兼容现有STM32接收缓冲")
        async with self.write_lock:
            if self.session.source == "demo":
                parts = payload.decode("ascii").strip("[]").split(",")
                if parts[0] == "set":
                    key, value = parts[2], float(parts[3])
                    limits = {"filter.alpha": (0, 1), "filter.cutoff": (0.01, 500),
                              "pid.kp": (0, 10000), "pid.ki": (0, 10000), "pid.kd": (0, 10000)}
                    if key not in limits or not limits[key][0] <= value <= limits[key][1]:
                        self.session.ingest(f"[nack,{parts[1]},unsupported_or_range]".encode())
                        return
                    self.demo_params[key] = value
                    await asyncio.sleep(0.15)
                    self.session.ingest(f"[ack,{parts[1]},{key},{value}]".encode())
                elif parts[0] == "get":
                    for key, value in self.demo_params.items():
                        self.session.ingest(f"[param,{parts[1]},{key},{value}]".encode())
                return
            client, characteristic = self.client, self.write_char
            if not client or not client.is_connected or not characteristic:
                raise ValueError("蓝牙未连接")
            response = "write" in characteristic.properties
            # Conservative 20-byte chunks work even with the default ATT MTU.
            for offset in range(0, len(payload), 20):
                await asyncio.wait_for(client.write_gatt_char(characteristic, payload[offset:offset + 20], response=response), 5)
                await asyncio.sleep(0.025)

    async def send_parameter(self, seq, payload, generation=None):
        try:
            await self.send(payload, generation)
        except Exception as exc:
            self.session.failed_set(seq, exc)
            raise

    async def demo(self):
        async with self.mode_lock:
            await self._disconnect()
            self.demo_params = {"filter.alpha": 0.02, "filter.cutoff": 20.0,
                                "pid.kp": 1.2, "pid.ki": 0.1, "pid.kd": 0.03}
            self.session.link("demo", True, "演示数据 · 不是硬件测量")
            self.background = asyncio.create_task(self._demo_loop())
            await self._send(b"[get,0]")
            self.notices.put(("status", "演示中：所有曲线和参数确认均为模拟"))

    async def _demo_loop(self):
        started = time.monotonic()
        previous = started
        estimate = 0
        while True:
            t = time.monotonic() - started
            now = time.monotonic()
            dt = now - previous
            previous = now
            target = 15 if int(t / 5) % 2 else 0
            clean = 12 * math.sin(t * 1.2)
            raw = clean + random.gauss(0, 1.5)
            gyro = 14.4 * math.cos(t * 1.2) + 0.2
            alpha = self.demo_params["filter.alpha"]
            estimate += gyro * dt
            estimate += alpha * (raw - estimate)
            frame = f"[data,{int(t * 1000)},roll_acc={raw:.4f},roll_est={estimate:.4f},target={target},gyro_y={gyro:.4f}]"
            self.session.ingest(frame.encode())
            await asyncio.sleep(0.02)

    async def replay(self, path):
        async with self.mode_lock:
            await self._disconnect()
            self.session.link("replay", False, f"日志回放 / {Path(path).name}")
            self.background = asyncio.create_task(self._replay_loop(path))

    async def _replay_loop(self, path):
        previous = None
        with Path(path).open(encoding="utf-8") as file:
            for line in file:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get("type") != "sample" or "frame" not in item:
                    continue
                stamp = float(item.get("t", 0))
                if previous is not None:
                    await asyncio.sleep(max(0, min(0.5, stamp - previous)))
                self.session.ingest(item["frame"].encode())
                previous = stamp
        self.notices.put(("status", "回放完成；数据保留在图中，可导出分析包"))

    def close(self):
        try:
            asyncio.run_coroutine_threadsafe(self.disconnect(), self.loop).result(timeout=12)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)

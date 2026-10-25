"""Run from the repository root: python -m unittest discover -v"""
import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path

from protocol import FrameParser, decode, set_command
from filters import FilterPreview
from session import Session
from transport import Transport


class ProtocolTests(unittest.TestCase):
    def test_fragmented_and_coalesced_frames(self):
        parser = FrameParser()
        self.assertEqual(parser.feed(b"noise[plot,1"), [])
        frames = parser.feed(b",2][plot,3,4]")
        self.assertEqual(len(frames), 2)
        self.assertEqual(decode(frames[0])["values"], {"roll_acc": 1, "roll_est": 2})

    def test_resynchronization_and_bounds(self):
        parser = FrameParser(max_size=20)
        frames = parser.feed(b"[" + b"x" * 25 + b"][bad[plot,7]")
        self.assertEqual(frames, ["[plot,7]"])
        self.assertEqual(parser.discarded, 2)

    def test_finite_values_and_channel_validation(self):
        for frame in ("[plot,nan]", "[plot,inf]", "[data,5,a=1,a=2]", "[data,5,bad key=1]"):
            with self.assertRaises(ValueError):
                decode(frame)
        self.assertEqual(decode("[data,10,gyro_y=2.5]")["device_ms"], 10)
        self.assertEqual(set_command(2, "filter.alpha", 0.02), b"[set,2,filter.alpha,0.02]")
        with self.assertRaises(ValueError):
            set_command(1, "x],evil", 1)


class FilterTests(unittest.TestCase):
    def preview(self, kind, **kwargs):
        engine = FilterPreview()
        engine.configure({**engine.config, "kind": kind, **kwargs})
        return engine

    def test_ema_and_restart(self):
        engine = self.preview("一阶低通 EMA", alpha=0.1)
        self.assertEqual(engine.update({"roll_acc": 0}, 0, None), 0)
        self.assertAlmostEqual(engine.update({"roll_acc": 10}, 0.1, None), 1)
        self.assertEqual(engine.update({"roll_acc": 3}, 5, None), 3)

    def test_median_rejects_single_impulse(self):
        engine = self.preview("中值滤波", window=3)
        outputs = [engine.update({"roll_acc": v}, i * 0.1, None) for i, v in enumerate([1, 1, 100])]
        self.assertEqual(outputs[-1], 1)

    def test_complementary_uses_device_clock_and_wrap(self):
        engine = self.preview("互补滤波", alpha=0.02)
        self.assertEqual(engine.update({"roll_acc": 179, "gyro_y": 0}, 10, 1000), 179)
        result = engine.update({"roll_acc": -179, "gyro_y": 0}, 20, 1005)
        self.assertAlmostEqual(result, 179.04)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.session = Session(Path(self.temp.name))
        self.session.link("ble", True, "test")

    def tearDown(self):
        self.session.close()
        self.temp.cleanup()

    def test_ack_only_after_matching_request(self):
        seq, _ = self.session.prepare_set("pid.kp", 2)
        self.session.ingest(f"[ack,{seq},pid.ki,2]".encode())
        self.assertEqual(self.session.params["pid.kp"]["status"], "pending")
        self.session.ingest(f"[ack,{seq},pid.kp,1.99]".encode())
        self.assertEqual(self.session.params["pid.kp"]["status"], "confirmed")
        self.assertEqual(self.session.params["pid.kp"]["value"], 1.99)

    def test_timeout_does_not_claim_applied(self):
        seq, _ = self.session.prepare_set("pid.kp", 2)
        self.session.pending[seq]["sent"] -= 6
        self.session.tick()
        self.assertEqual(self.session.params["pid.kp"]["status"], "timeout")

    def test_parameter_query_cannot_confirm_pending_write(self):
        seq, _ = self.session.prepare_set("pid.kp", 2)
        self.session.ingest(b"[param,99,pid.kp,1]")
        self.assertEqual(self.session.params["pid.kp"]["status"], "pending")
        self.assertIn(seq, self.session.pending)

    def test_disconnect_keeps_data_and_report_provenance(self):
        self.session.ingest(b"[plot,1,2][plot,2,3]")
        self.session.link("offline", False, "disconnected")
        self.assertEqual(len(self.session.copy_samples()), 2)
        path = self.session.report()
        self.assertIn("ble", path.read_text(encoding="utf-8"))
        self.session.tick()
        snapshot = json.loads((self.session.folder / "snapshot.json").read_text(encoding="utf-8"))
        self.assertFalse(snapshot["connected"])
        self.assertEqual(snapshot["data_source"], "ble")
        with self.assertRaises(ValueError):
            self.session.prepare_set("pid.kp", 1)

    def test_log_tracks_derived_preview(self):
        self.session.preview.configure({**self.session.preview.config, "kind": "一阶低通 EMA"})
        self.session.ingest(b"[plot,1,2]")
        self.assertEqual(self.session.latest["local.preview"], 1)
        self.session.tick()
        last = json.loads((self.session.folder / "frames.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["values"]["local.preview"], 1)

    def test_chart_window_keeps_only_visible_history(self):
        for i in range(100):
            self.session.samples.append({"t": i, "values": {"x": i}})
        self.assertEqual(len(self.session.chart_samples(10)), 11)
        self.assertEqual(self.session.copy_samples(3)[0]["t"], 97)


class TransportTests(unittest.TestCase):
    def test_demo_parameter_roundtrip_and_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            session = Session(Path(folder))
            transport = Transport(session)
            try:
                transport.submit(transport.demo()).result(3)
                seq, payload = session.prepare_set("filter.alpha", 0.25)
                transport.submit(transport.send_parameter(seq, payload, transport.generation)).result(3)
                self.assertEqual(session.params["filter.alpha"]["status"], "confirmed")
                self.assertEqual(session.source, "demo")
                replay = Path(folder) / "fixture.jsonl"
                replay.write_text(json.dumps({"type": "sample", "t": 0, "frame": "[plot,3,4]"}) + "\n", encoding="utf-8")
                transport.submit(transport.replay(replay)).result(3)
                for _ in range(30):
                    if session.latest.get("roll_acc") == 3:
                        break
                    time.sleep(0.01)
                self.assertEqual(session.latest["roll_acc"], 3)
                self.assertEqual(session.source, "replay")
            finally:
                transport.close()
                session.close()


if __name__ == "__main__":
    unittest.main()

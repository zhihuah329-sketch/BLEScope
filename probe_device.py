"""Read-only hardware check: scan, subscribe to telemetry, save a session, exit."""
import argparse
import json
import time
from pathlib import Path

from session import Session
from transport import Transport, NOTIFY_UUID, WRITE_UUID


def main(address):
    session = Session(Path(__file__).resolve().parent / "sessions")
    transport = Transport(session)
    try:
        transport.submit(transport.scan()).result(timeout=15)
        transport.submit(transport.connect(address, NOTIFY_UUID, WRITE_UUID)).result(timeout=45)
        time.sleep(8)
        session.tick()
        report = session.report()
        snapshot = session.snapshot()
        print(json.dumps({"connected": snapshot["connected"], "samples": snapshot["sample_count"],
                          "receive_rate_hz": snapshot["receive_rate_hz"], "latest": snapshot["latest"],
                          "invalid_frames": snapshot["invalid_frames"], "report": str(report)}, ensure_ascii=False))
    finally:
        transport.close()
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", required=True)
    main(parser.parse_args().address)

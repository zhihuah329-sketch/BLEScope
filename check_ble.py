"""Read-only BLE discovery diagnostic. Does not connect or write to devices."""
import asyncio
import json
from bleak import BleakScanner


async def main():
    devices = await BleakScanner.discover(timeout=6, return_adv=True)
    rows = [{"name": adv.local_name or device.name, "address": address, "rssi": adv.rssi}
            for address, (device, adv) in devices.items()
            if "CH914" in (adv.local_name or device.name or "").upper()]
    print(json.dumps({"ble_devices_seen": len(devices), "ch914x": rows}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())

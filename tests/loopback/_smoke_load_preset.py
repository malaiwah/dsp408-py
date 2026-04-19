"""Verify load_preset_by_name (the lightweight cmd=0x00 path)."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    name_before = dsp.read_preset_name()
    print(f"  preset before: {name_before!r}")

    dsp.load_preset_by_name("Custom")
    time.sleep(0.3)
    name_after = dsp.read_preset_name()
    print(f"  preset after load_preset_by_name('Custom'): {name_after!r}")
    assert name_after.startswith("Custom"), f"name didn't change to Custom"
    print("  ✓ load_preset_by_name works")

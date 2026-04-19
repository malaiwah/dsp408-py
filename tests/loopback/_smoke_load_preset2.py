"""Test load_preset_by_name by toggling between two names."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    print(f"  initial preset: {dsp.read_preset_name()!r}")

    # Use existing write_preset_name first (known to work)
    dsp.write_preset_name("Test1")
    time.sleep(0.5)
    print(f"  after write_preset_name('Test1'): {dsp.read_preset_name()!r}")

    # Now use our new load_preset_by_name
    dsp.load_preset_by_name("Bureau")
    time.sleep(0.5)
    print(f"  after load_preset_by_name('Bureau'): {dsp.read_preset_name()!r}")

    # Toggle once more
    dsp.load_preset_by_name("Custom")
    time.sleep(0.5)
    print(f"  after load_preset_by_name('Custom'): {dsp.read_preset_name()!r}")

"""Replay the EXACT bytes from captures/load_loaddisk_save_preset_bureau.pcapng
frames 2019..2027 to verify the device acks multi-frame writes via cmd=0x10000."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices
from dsp408.transport import Transport

# Frames captured from the GUI (5x 64-byte HID frames for ch0 full-state write)
FRAMES = [
    # Frame 2019 (first frame) — 64 bytes
    bytes.fromhex(
        "808080eea10100040000010028011f0058023400000041005802340000007d00580234000000fa00580234000000f401580234000000e803580234000000d007"
    ),
    # Frame 2021 (cont 1)
    bytes.fromhex(
        "580234000000a00f580234000000401f580234000000803e580234000000c800580234000000fa005802340000003b015802340000009001580234000000f401"
    ),
    # Frame 2023 (cont 2)
    bytes.fromhex(
        "58023400000076025802340000002003580234000000e803580234000000e2045802340000004006580234000000d007580234000000c4095802340000004e0c"
    ),
    # Frame 2025 (cont 3)
    bytes.fromhex(
        "580234000000a00f58023400000088135802340000009c18580234000000401f5802340000001027580234000000d430580234000000803e580234000000204e"
    ),
    # Frame 2027 (cont 4, partial + padding)
    bytes.fromhex(
        "580234000000010058020000000164000003204e00036400640000000000a4013800f4010000a4013800f401000220202020202020000aaa0000000000000000"
    ),
]

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    print(f"Sending {len(FRAMES)} HID frames (5x 64 bytes), each {len(FRAMES[0])} bytes")

    t: Transport = dsp._t
    # Send all frames back-to-back, no delay
    for i, f in enumerate(FRAMES):
        assert len(f) == 64, f"frame {i}: {len(f)} bytes"
        t.send_frame(f)
    print("All 5 frames sent. Waiting for ack...")

    # Read for up to 3 seconds
    ack = t.read_response(timeout_ms=3000)
    if ack is None:
        print("  ✗ NO ACK received within 3s")
    else:
        print(f"  ✓ ACK: dir=0x{ack.direction:02x} cmd=0x{ack.cmd:04x} len={ack.payload_len}")

"""Diagnose multi-frame write — compare what we send vs what GUI sent,
and inspect device state immediately after."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices
from dsp408.protocol import build_frames_multi, DIR_WRITE
from dsp408.transport import Transport

GUI_FRAMES = [
    bytes.fromhex("808080eea10100040000010028011f0058023400000041005802340000007d00580234000000fa00580234000000f401580234000000e803580234000000d007"),
    bytes.fromhex("580234000000a00f580234000000401f580234000000803e580234000000c800580234000000fa005802340000003b015802340000009001580234000000f401"),
    bytes.fromhex("58023400000076025802340000002003580234000000e803580234000000e2045802340000004006580234000000d007580234000000c4095802340000004e0c"),
    bytes.fromhex("580234000000a00f58023400000088135802340000009c18580234000000401f5802340000001027580234000000d430580234000000803e580234000000204e"),
    bytes.fromhex("580234000000010058020000000164000003204e00036400640000000000a4013800f4010000a4013800f401000220202020202020000aaa0000000000000000"),
]
GUI_PAYLOAD = GUI_FRAMES[0][14:64] + GUI_FRAMES[1] + GUI_FRAMES[2] + GUI_FRAMES[3] + GUI_FRAMES[4][:54]

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    t: Transport = dsp._t

    # Test A: replay GUI bytes verbatim and read state
    print("--- TEST A: replay GUI bytes verbatim ---")
    for f in GUI_FRAMES:
        t.send_frame(f)
    ack = t.read_response(timeout_ms=3000)
    print(f"  ACK: dir=0x{ack.direction:02x}" if ack else "  NO ACK")
    time.sleep(0.3)
    blob = bytes(dsp.read_channel_state(0))
    print(f"  ch0 first 64 bytes after replay: {blob[:64].hex()}")
    print(f"  ch0 bytes 48..63:               {blob[48:64].hex()}")
    print(f"  expected from GUI payload[48..63]: {GUI_PAYLOAD[48:64].hex()}")

    # Test B: build frames using our code and send those
    print("\n--- TEST B: send via build_frames_multi ---")
    our_frames = build_frames_multi(direction=DIR_WRITE, seq=0, cmd=0x10000,
                                     data=GUI_PAYLOAD, category=0x04)
    print(f"  our frames: {len(our_frames)} (vs GUI: {len(GUI_FRAMES)})")
    for i in range(min(len(our_frames), len(GUI_FRAMES))):
        same = our_frames[i] == GUI_FRAMES[i]
        print(f"  frame {i}: {'✓ identical' if same else '✗ differs'}")
    # Now send ours
    for f in our_frames:
        t.send_frame(f)
    ack = t.read_response(timeout_ms=3000)
    print(f"  ACK: dir=0x{ack.direction:02x}" if ack else "  NO ACK")
    time.sleep(0.3)
    blob2 = bytes(dsp.read_channel_state(0))
    print(f"  ch0 bytes 48..63 after our write: {blob2[48:64].hex()}")
    if blob == blob2:
        print("  ✓ state matches replay state — our code is equivalent!")
    else:
        diffs = [(i, blob[i], blob2[i]) for i in range(min(len(blob), len(blob2)))
                 if blob[i] != blob2[i]]
        print(f"  ✗ state differs in {len(diffs)} bytes; first 5: {diffs[:5]}")

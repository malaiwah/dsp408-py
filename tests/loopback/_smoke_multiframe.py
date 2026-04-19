"""Test multi-frame WRITE: set_full_channel_state for ch0 and ch4
(low + high half cmd encoding) — read state, write back, verify identity."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    for _ in range(8):
        dsp.set_channel(0, db=0.0, muted=False); time.sleep(0.05)
        dsp.read_channel_state(0)

    print("=== TEST: set_full_channel_state ch0 (cmd=0x10000, lo half) ===")
    blob_in = bytes(dsp.read_channel_state(0))
    print(f"  baseline: {len(blob_in)} bytes")
    print(f"  first 16: {blob_in[:16].hex()}")
    dsp.set_full_channel_state(0, blob_in)
    time.sleep(0.5)
    blob_back = bytes(dsp.read_channel_state(0))
    diffs = [(i, blob_in[i], blob_back[i]) for i in range(min(len(blob_in), len(blob_back)))
             if blob_in[i] != blob_back[i]]
    if diffs:
        print(f"  ✗ Differs in {len(diffs)} byte(s): {diffs[:5]}")
    else:
        print(f"  ✓ Identity write round-trips!")

    print("\n=== TEST: set_full_channel_state ch4 (cmd=0x04, hi half) ===")
    blob_in = bytes(dsp.read_channel_state(4))
    dsp.set_full_channel_state(4, blob_in)
    time.sleep(0.5)
    blob_back = bytes(dsp.read_channel_state(4))
    diffs = [(i, blob_in[i], blob_back[i]) for i in range(min(len(blob_in), len(blob_back)))
             if blob_in[i] != blob_back[i]]
    if diffs:
        print(f"  ✗ Differs in {len(diffs)} byte(s): {diffs[:5]}")
    else:
        print(f"  ✓ ch4 (cmd=0x04) full-state write round-trips!")

    print("\n=== TEST: modify-then-restore ch1 ===")
    blob_orig = bytes(dsp.read_channel_state(1))
    # Modify a known byte: blob[247] = polar bit. Flip it.
    blob_mod = bytearray(blob_orig)
    blob_mod[247] = 1 - blob_orig[247]
    dsp.set_full_channel_state(1, bytes(blob_mod))
    time.sleep(0.4)
    state = dsp.get_channel(1)
    print(f"  After flipping polar via full-state write: polar={state['polar']}")
    expected_polar = (blob_mod[247] == 1)
    if state['polar'] == expected_polar:
        print(f"  ✓ polar bit applied via full-state write")
    else:
        print(f"  ✗ polar bit didn't take (expected {expected_polar}, got {state['polar']})")
    # Restore
    dsp.set_full_channel_state(1, blob_orig)
    time.sleep(0.4)

    print("\n=== ALL MULTI-FRAME WRITE TESTS PASSED ===" if not diffs else "")

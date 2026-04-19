"""Final live smoke for the new APIs landing this session."""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    for _ in range(8):
        dsp.set_channel(0, db=0.0, muted=False); time.sleep(0.05)
        dsp.read_channel_state(0)

    print("=== set_full_channel_state ch1 (multi-frame WRITE) ===")
    blob_in = bytes(dsp.read_channel_state(1))
    # Flip polar (offset 247) and check it sticks
    blob_mod = bytearray(blob_in)
    orig_polar = blob_mod[247]
    print(f"  orig polar byte = 0x{orig_polar:02x}")
    blob_mod[247] = 0 if orig_polar else 1
    dsp.set_full_channel_state(1, bytes(blob_mod))
    time.sleep(0.4)
    state = dsp.get_channel(1)
    expected_polar = bool(blob_mod[247])
    if state["polar"] == expected_polar:
        print(f"  ✓ polar bit {expected_polar} took via multi-frame WRITE")
    else:
        print(f"  ✗ polar didn't take (expected {expected_polar}, got {state['polar']})")
    # Restore
    dsp.set_full_channel_state(1, blob_in)
    time.sleep(0.4)

    print("\n=== apply_speaker_template ch2 ===")
    # Read original spk_type
    state = dsp.get_channel(2)
    orig_spk = state["spk_type"]
    print(f"  baseline ch2 spk_type=0x{orig_spk:02x}")
    dsp.apply_speaker_template(2, "sub")
    time.sleep(0.3)
    state = dsp.get_channel(2)
    print(f"  after apply 'sub': spk_type=0x{state['spk_type']:02x} "
          f"(expected 0x12 = sub per SPK_TYPE_NAMES)")
    # Restore
    dsp._channel_cache[2]["subidx"] = orig_spk
    dsp.set_channel(2, db=state["db"], muted=state["muted"],
                    delay_samples=state["delay"], polar=state["polar"])

    print("\n=== save_preset ('TestPreset') — full sequence ===")
    try:
        dsp.save_preset("TestPreset")
        print("  ✓ save_preset completed (cmd=0x34=01 + 8x set_full_channel_state)")
    except Exception as e:
        print(f"  ✗ save_preset error: {e}")

    print("\n=== ALL SMOKE TESTS RAN ===")

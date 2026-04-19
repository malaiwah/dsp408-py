"""Sharper INPUT MISC probe — figure out where DataID=9 writes actually land.

In the previous probe, cmd=0x0900 cat=0x03 with distinctive bytes only
showed blob[286] (= checksum) changing in input ch0's readback. That's
suspicious. Try:
  1. Multiple input channels (maybe ch0 special)
  2. After a warm-up loop (8x like outputs)
  3. Compare BEFORE the write to the EXACT POST-WRITE READ
  4. Try DataID=9 on ALL inputs and look at differences

Also: re-baseline carefully and look at the FULL blob diff each time.
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()

    def read_input(ch):
        return bytes(dsp.read_raw(cmd=0x7700 + ch, category=0x03).payload)

    def diff(a, b):
        return [(i, a[i], b[i]) for i in range(min(len(a), len(b))) if a[i] != b[i]]

    print("=== DataID=9 (INPUT MISC) writes — try ALL inputs + warm-up ===")
    # Baseline ALL inputs first
    bases = {ch: read_input(ch) for ch in range(8)}

    print("\nBaselines (input ch defaults):")
    for ch in range(8):
        print(f"  ch{ch}: first 32 bytes = {bases[ch][:32].hex()}")
        print(f"        bytes 80..96 = {bases[ch][80:96].hex()}")
        print(f"        bytes 246..288 = {bases[ch][246:].hex()}")

    distinctive_misc = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x77, 0x88])
    print(f"\nDistinctive MISC payload: {distinctive_misc.hex()}")

    for ch in range(8):
        print(f"\n--- DataID=9 write to input ch{ch} (cmd=0x09{ch:02x} cat=0x03) ---")
        # Warm-up: write 4 times before the test write
        for _ in range(4):
            try:
                dsp.write_raw(cmd=0x0900 + ch, data=bytes(8), category=0x03)
                time.sleep(0.05)
            except Exception:
                pass
        # The actual distinctive write
        try:
            dsp.write_raw(cmd=0x0900 + ch, data=distinctive_misc, category=0x03)
        except Exception as e:
            print(f"  WRITE ERROR: {e}")
            continue
        time.sleep(0.3)
        after = read_input(ch)
        d = diff(bases[ch], after)
        if d:
            # Group consecutive offsets
            ranges = []
            start = end = d[0][0]
            for off, _, _ in d[1:]:
                if off == end + 1:
                    end = off
                else:
                    ranges.append((start, end))
                    start = end = off
            ranges.append((start, end))
            for s, e in ranges:
                changed = bytes(after[s:e+1])
                print(f"  blob[{s:>3}..{e:>3}] ({e-s+1} bytes): "
                      f"{bases[ch][s:e+1].hex()} → {changed.hex()}")
        else:
            print("  NO BYTES CHANGED")
        # Restore for next test
        try:
            dsp.write_raw(cmd=0x0900 + ch, data=bytes(8), category=0x03)
        except Exception:
            pass
        time.sleep(0.1)

    # Also check: does writing DataID=10 (between 9 and 11) do something?
    print("\n=== Probe DataID=10 (between MISC and noisegate) ===")
    base = read_input(0)
    dist = bytes([0x10, 0x11, 0x12, 0x13, 0x14, 0x15, 0x16, 0x17])
    try:
        dsp.write_raw(cmd=0x0A00, data=dist, category=0x03)
        time.sleep(0.3)
        after = read_input(0)
        d = diff(base, after)
        if d:
            for off, b1, b2 in d[:20]:
                print(f"  blob[{off:>3}]: 0x{b1:02x} → 0x{b2:02x}")
        else:
            print("  NO BYTES CHANGED for DataID=10")
    except Exception as e:
        print(f"  ERROR: {e}")

    # Cleanup: zero everything
    print("\nCleanup...")
    for ch in range(8):
        try:
            dsp.write_raw(cmd=0x0900 + ch, data=bytes(8), category=0x03)
            time.sleep(0.02)
        except Exception:
            pass
print("\n=== DONE ===")

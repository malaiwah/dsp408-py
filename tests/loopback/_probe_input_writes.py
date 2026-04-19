"""Pin down the input-processing layout + verify writes round-trip.

Tests:
  1. Distinctive-byte injection: write a unique 8-byte EQ band 0 to input 0
     via cmd=0x0000 cat=0x03 (DataID=0, CID=0), then read input 0 back.
     Find which offsets of the 288-byte blob actually changed.
  2. Try DataID=9 (input MISC) write — distinctive 8 bytes; see what blob
     offsets land at.
  3. Try DataID=11 (input noisegate) — distinctive 8 bytes.
  4. Decode the full 288-byte input state structure.
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()

    def read_input(ch):
        return bytes(dsp.read_raw(cmd=0x7700 + ch, category=0x03).payload)

    def fmt_diff(before, after):
        diffs = [(i, before[i], after[i])
                 for i in range(min(len(before), len(after)))
                 if before[i] != after[i]]
        return diffs

    print("=== Baseline read of input ch 0 ===")
    base = read_input(0)
    print(f"  len={len(base)}, first 64 bytes: {base[:64].hex()}")
    print(f"  bytes 240..end: {base[240:].hex()}")

    # --- Test 1: write distinctive EQ band 0 to input 0 (cmd=0x0000 cat=0x03) ---
    print("\n=== Test 1: write distinctive EQ band 0 to input ch 0 (cmd=0x0000 cat=0x03) ===")
    distinctive = bytes([0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF, 0x77, 0x88])
    try:
        dsp.write_raw(cmd=0x0000, data=distinctive, category=0x03)
        time.sleep(0.3)
        after = read_input(0)
        diffs = fmt_diff(base, after)
        if diffs:
            print(f"  Bytes that changed:")
            for off, b1, b2 in diffs:
                print(f"    blob[{off:>3}]: 0x{b1:02x} -> 0x{b2:02x}")
        else:
            print("  NO BYTES CHANGED")
    except Exception as e:
        print(f"  ERROR: {e}")

    base = read_input(0)  # Re-baseline

    # --- Test 2: write distinctive band 5 (cmd=0x0500 cat=0x03) ---
    print("\n=== Test 2: write distinctive EQ band 5 to input ch 0 (cmd=0x0500 cat=0x03) ===")
    try:
        dsp.write_raw(cmd=0x0500, data=distinctive, category=0x03)
        time.sleep(0.3)
        after = read_input(0)
        diffs = fmt_diff(base, after)
        if diffs:
            print(f"  Bytes that changed:")
            for off, b1, b2 in diffs:
                print(f"    blob[{off:>3}]: 0x{b1:02x} -> 0x{b2:02x}")
        else:
            print("  NO BYTES CHANGED")
    except Exception as e:
        print(f"  ERROR: {e}")

    base = read_input(0)

    # --- Test 3: write distinctive MISC (cmd=0x0900 cat=0x03) ---
    print("\n=== Test 3: write distinctive INPUT MISC (cmd=0x0900 cat=0x03) ===")
    misc = bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8])
    try:
        dsp.write_raw(cmd=0x0900, data=misc, category=0x03)
        time.sleep(0.3)
        after = read_input(0)
        diffs = fmt_diff(base, after)
        if diffs:
            print(f"  Bytes that changed:")
            for off, b1, b2 in diffs:
                print(f"    blob[{off:>3}]: 0x{b1:02x} -> 0x{b2:02x}")
        else:
            print("  NO BYTES CHANGED")
    except Exception as e:
        print(f"  ERROR: {e}")

    base = read_input(0)

    # --- Test 4: write distinctive NOISEGATE (cmd=0x0B00 cat=0x03) ---
    print("\n=== Test 4: write distinctive INPUT NOISEGATE (cmd=0x0B00 cat=0x03) ===")
    nz = bytes([0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8])
    try:
        dsp.write_raw(cmd=0x0B00, data=nz, category=0x03)
        time.sleep(0.3)
        after = read_input(0)
        diffs = fmt_diff(base, after)
        if diffs:
            print(f"  Bytes that changed:")
            for off, b1, b2 in diffs:
                print(f"    blob[{off:>3}]: 0x{b1:02x} -> 0x{b2:02x}")
        else:
            print("  NO BYTES CHANGED")
    except Exception as e:
        print(f"  ERROR: {e}")

    # --- Test 5: how many EQ bands exist on input? Try DataID 0..30 ---
    print("\n=== Test 5: try each EQ band DataID 0..30 — find max valid ===")
    base = read_input(0)
    distinctive2 = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
    for band in range(0, 31, 1):
        cmd = (band << 8) | 0  # CID=0, DataID=band
        try:
            dsp.write_raw(cmd=cmd, data=distinctive2, category=0x03)
        except Exception as e:
            print(f"  band={band}: WRITE ERROR {e}")
            continue
        time.sleep(0.05)
    time.sleep(0.3)
    after = read_input(0)
    diffs = fmt_diff(base, after)
    print(f"  Total bytes changed across all 31 band attempts: {len(diffs)}")
    print(f"  Changed offset ranges:")
    if diffs:
        # Group consecutive offsets
        groups = []
        s = e = diffs[0][0]
        for off, _, _ in diffs[1:]:
            if off == e + 1:
                e = off
            else:
                groups.append((s, e))
                s = e = off
        groups.append((s, e))
        for s, e in groups:
            print(f"    offsets {s}..{e} ({e - s + 1} bytes)")

    # --- Test 6: try INPUT volume / mute / polar via DataID=9 reads ---
    # The MISC block is 8 bytes per leon. Decode what fields are where.
    # Restore everything to safe defaults first
    print("\n=== Test 6: decode INPUT MISC default layout ===")
    print("Reading current input ch 0 state, examining bytes around the MISC area...")
    state = read_input(0)
    # MISC was written at offsets that Test 3 revealed. We saw the offsets earlier.
    # Just dump the whole blob as 36 rows of 8 bytes
    print("  Full input ch 0 blob, 8 bytes per row:")
    for i in range(0, len(state), 8):
        row = state[i:i+8]
        print(f"    [{i:>3}..{i+7:>3}]: {row.hex(' ')}  {' '.join(chr(b) if 32<=b<127 else '.' for b in row)}")

print("\n=== DONE ===")

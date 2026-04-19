"""Gate question: does cat=0x03 (DataType=3 = MUSIC = input processing) work
on USB? If yes, we have a whole new control surface (input gain/mute/delay/
polar/EQ/noisegate per the 4 RCA + 4 high-level inputs).

Test ladder, fail-fast:
  1. read cmd=0x77 cat=0x03 (input full-state read, mirror of output 0x77 read)
     - if succeeds with data → DataType=3 is wire-supported
  2. read cmd=0x09 cat=0x03 (input MISC read, DataID=9)
  3. read cmd=0x0B cat=0x03 (input noisegate, DataID=11)
  4. write cmd=0x0900 cat=0x03 with 8-byte payload, see if device acks
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
from dsp408 import Device, enumerate_devices
from dsp408.protocol import parse_frame

info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()

    def try_read(cmd, cat, label):
        try:
            r = dsp.read_raw(cmd=cmd, category=cat, timeout_ms=1000)
            payload = bytes(r.payload)
            print(f"  {label}: dir=0x{r.direction:02x} len={len(payload)} "
                  f"data={payload.hex()[:80]}")
            return r
        except Exception as e:
            print(f"  {label}: ERROR {type(e).__name__}: {e}")
            return None

    print("=== Test 1: read cmd=0x7700..0x7707 with cat=0x03 (input full-state) ===")
    for ch in range(8):
        try_read(0x7700 + ch, 0x03, f"cmd=0x77{ch:02x} cat=0x03 (input ch{ch})")

    print("\n=== Test 2: read cmd=0x0900..0x0907 cat=0x03 (input MISC) ===")
    for ch in range(4):
        try_read(0x0900 + ch, 0x03, f"cmd=0x09{ch:02x} cat=0x03")

    print("\n=== Test 3: read cmd=0x0B00 cat=0x03 (input noisegate ch 0) ===")
    try_read(0x0B00, 0x03, "cmd=0x0B00 cat=0x03 (input noisegate ch0)")

    print("\n=== Test 4: write cmd=0x0900 cat=0x03 with all-zero payload ===")
    try:
        # Distinctive but harmless: try writing default-ish input MISC values
        payload = bytes([
            0,    # feedback
            0,    # polar
            0,    # mode
            0,    # mute
            0, 0, # delay le16
            0,    # volume
            0,    # spare
        ])
        r = dsp.write_raw(cmd=0x0900, data=payload, category=0x03)
        print(f"  WRITE ack: dir=0x{r.direction:02x} cmd=0x{r.cmd:04x} "
              f"len={r.payload_len}")
    except Exception as e:
        print(f"  ERROR {type(e).__name__}: {e}")

    print("\n=== Test 5: try other DataType candidates (cat byte) ===")
    # leon mentions DataType ∈ {3, 4, 9}. Existing: 4=output, 9=state.
    # 3 just tested. Try a couple wild values to see device behavior.
    for cat in (0x01, 0x02, 0x03, 0x05, 0x07, 0x08, 0x0A):
        try_read(0x7700, cat, f"cmd=0x7700 cat=0x{cat:02x}")

print("\n=== DONE ===")

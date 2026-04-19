"""Compressor probe attempt #2 — try every combination of extreme settings
to find ANY condition where compression actually engages.

Theories to test:
  A) Threshold encoding might not be raw u8; try every value 0..255 with hot signal
  B) attack might be in samples, ms, or 0.1 ms units — try aggressive values
  C) Maybe enable byte requires a magic value other than 0x01
  D) Maybe the compressor is bypassed unless some OTHER blob byte is set
     (e.g. byte_252 — was hypothesized as eq_mode but disproved; could it be
     compressor_enable instead?)
  E) Maybe writing 0x230X alone doesn't trigger audio-engine reload; needs
     a follow-up "apply" cmd — try set_master afterwards as a kicker
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
import numpy as np
from audio_io import DEFAULT_SR, mono_to_stereo, play_and_record, sine
from dsp408 import Device, enumerate_devices
from dsp408.protocol import (CMD_WRITE_COMPRESSOR_BASE, CMD_WRITE_CHANNEL_BASE,
                              CHANNEL_SUBIDX, CAT_PARAM)

SR = DEFAULT_SR
DSP_OUT = 1


def setup(dsp):
    dsp.set_master(db=0.0, muted=False)
    for _ in range(8):
        dsp.set_channel(DSP_OUT, db=0.0, muted=False); time.sleep(0.05)
        dsp.read_channel_state(DSP_OUT)
    for ch in range(8):
        if ch != DSP_OUT:
            dsp.set_channel(ch, db=0.0, muted=True)
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
    dsp.set_routing(DSP_OUT, in1=True, in2=False, in3=False, in4=False)
    dsp.set_crossover(DSP_OUT, 10, 0, 0, 22000, 0, 0)
    for b in range(dsp.EQ_BAND_COUNT):
        dsp.set_eq_band(DSP_OUT, b, dsp.EQ_DEFAULT_FREQS_HZ[b], 0.0)
    time.sleep(0.3)


def rms_dbfs(x):
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    if rms < 1e-9: return -120.0
    return 20.0 * np.log10(rms * np.sqrt(2.0))


def measure(in_db=-3, dur=1.0):
    tone = sine(1000, dur, amp_dbfs=in_db)
    cap = play_and_record(mono_to_stereo(tone, left=True, right=False))
    body = slice(int(0.3 * SR), len(cap.in1) - int(0.1 * SR))
    return rms_dbfs(cap.in1[body])


def write_byte_252(dsp, ch, value):
    """Write the basic record with byte[6] (= blob[252]) set to a custom value.
    This was the disproven 'eq_mode' theory; let's see if it's actually
    a compressor enable."""
    si = CHANNEL_SUBIDX[ch]
    payload = bytes([1, 0, 0xff & 600, (600 >> 8) & 0xff, 0, 0,
                     value & 0xff, si & 0xff])
    cmd = CMD_WRITE_CHANNEL_BASE + ch
    dsp.write_raw(cmd=cmd, data=payload, category=CAT_PARAM)


info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    setup(dsp)

    print("=== Baseline (no compressor) ===")
    dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                       threshold=255, all_pass_q=420, enable=False)
    time.sleep(0.4)
    base_db = measure(in_db=-3)
    print(f"  in=-3 dBFS → out={base_db:+.2f} dBFS")

    # === Theory A: scan EVERY threshold value 0..255 with hot signal ===
    print("\n=== Theory A: full threshold sweep at -3 dBFS, atk=1ms, rel=10ms ===")
    print("  Looking for ANY drop > 0.5 dB from baseline")
    found_drop = False
    for thr in range(0, 256, 8):
        dsp.set_compressor(channel=DSP_OUT, attack_ms=1, release_ms=10,
                           threshold=thr, all_pass_q=420, enable=True)
        time.sleep(0.2)
        out = measure(in_db=-3, dur=0.8)
        diff = out - base_db
        if abs(diff) > 0.5:
            found_drop = True
            print(f"  thr={thr:>3}: out={out:+.2f} dBFS (diff {diff:+.2f}) ◄◄◄ MOVES")
    if not found_drop:
        print("  NO threshold value caused output to move > 0.5 dB")

    # === Theory C: try magic enable values ===
    print("\n=== Theory C: alternate enable bytes (in=-3 dBFS, thr=0) ===")
    for en_val in (0, 1, 2, 0x10, 0xFF):
        # Build raw 8-byte payload with custom enable byte
        payload = bytes([0xa4, 0x01, 0x01, 0x00, 0x0a, 0x00, 0x00, en_val])
        # Q=420, attack=1, release=10, threshold=0, enable=en_val
        dsp.write_raw(cmd=CMD_WRITE_COMPRESSOR_BASE + DSP_OUT,
                      data=payload, category=CAT_PARAM)
        time.sleep(0.4)
        out = measure(in_db=-3, dur=0.8)
        diff = out - base_db
        flag = " ◄ MOVES" if abs(diff) > 0.5 else ""
        print(f"  enable=0x{en_val:02x}: out={out:+.2f} dBFS (diff {diff:+.2f}){flag}")

    # === Theory D: byte_252 as compressor enable ===
    print("\n=== Theory D: byte_252 as compressor enable (in=-3 dBFS, comp ON) ===")
    # Set compressor active first
    dsp.set_compressor(channel=DSP_OUT, attack_ms=1, release_ms=10,
                       threshold=0, all_pass_q=420, enable=True)
    time.sleep(0.3)
    for b252 in (0, 1, 2, 0xFF):
        write_byte_252(dsp, DSP_OUT, b252)
        time.sleep(0.4)
        out = measure(in_db=-3, dur=0.8)
        diff = out - base_db
        flag = " ◄ MOVES" if abs(diff) > 0.5 else ""
        print(f"  byte_252=0x{b252:02x}: out={out:+.2f} dBFS (diff {diff:+.2f}){flag}")
    # Restore byte_252=0
    write_byte_252(dsp, DSP_OUT, 0)

    # === Theory E: kick with set_master after compressor write ===
    print("\n=== Theory E: kick with set_master after each compressor write ===")
    for thr in (255, 100, 50, 20, 0):
        dsp.set_compressor(channel=DSP_OUT, attack_ms=1, release_ms=10,
                           threshold=thr, all_pass_q=420, enable=True)
        time.sleep(0.1)
        # "Kick" — re-write master to force audio engine reload
        dsp.set_master(db=0.0, muted=False)
        time.sleep(0.3)
        out = measure(in_db=-3, dur=0.8)
        diff = out - base_db
        flag = " ◄ MOVES" if abs(diff) > 0.5 else ""
        print(f"  thr={thr:>3} +master kick: out={out:+.2f} dBFS (diff {diff:+.2f}){flag}")

    # === Theory F: try ch6/ch7 (the channels exercised in the windows capture) ===
    print("\n=== Theory F: try compressor on ch6 (matches capture's ch6 toggle) ===")
    # Reset OUT 1 routing, route IN1 → OUT 7 (ch6) instead
    dsp.set_routing(DSP_OUT, in1=False, in2=False, in3=False, in4=False)
    dsp.set_routing(6, in1=True, in2=False, in3=False, in4=False)
    dsp.set_channel(6, db=0.0, muted=False)
    dsp.set_crossover(6, 10, 0, 0, 22000, 0, 0)
    for _ in range(8):
        dsp.set_channel(6, db=0.0, muted=False); time.sleep(0.05)
        dsp.read_channel_state(6)
    # NOTE: ch6 isn't physically wired to Scarlett IN — output won't be measurable.
    # But we can at least check that our compressor write to ch6 goes through.
    dsp.set_compressor(channel=6, attack_ms=1, release_ms=10,
                       threshold=0, all_pass_q=420, enable=True)
    time.sleep(0.4)
    state6 = dsp.get_channel(6)
    print(f"  ch6 compressor readback: {state6['compressor']}")
    print(f"  (ch6 not physically wired to Scarlett — can't measure audio)")

    # Restore
    dsp.set_routing(6, in1=False, in2=False, in3=False, in4=False)
    dsp.set_channel(6, db=0.0, muted=True)
    dsp.set_routing(DSP_OUT, in1=True, in2=False, in3=False, in4=False)
    dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                       threshold=0, all_pass_q=420, enable=False)
    for ch in range(8):
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
        dsp.set_channel(ch, db=0.0, muted=False)
    print("\n=== DONE ===")

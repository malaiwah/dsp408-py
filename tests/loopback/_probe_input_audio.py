"""LAST-CALL audio validation of the input-processing subsystem.

We verified blob round-trips but never measured what input writes
ACTUALLY DO to the audio. Empirically test:

  1. Input MISC field semantics:
     a) polar — does it audibly invert phase?
     b) muted — does it audibly silence the input?
     c) volume — does the u8 attenuate? What's the dB curve?
     d) delay — does it actually delay? Units?
  2. Input EQ band — does writing +12 dB peak @ 1kHz produce a peak?
  3. Input noisegate — does it actually gate?
  4. Bonus: set_full_channel_state delivers polar bit AUDIBLY (end-to-
     end verification of multi-frame WRITE)
  5. Bonus: apply_speaker_template audibly changes channel behavior

Wire path: Scarlett OUT 1 → DSP IN 1 → DSP OUT 2 → Scarlett IN 1.
So input writes target DSP_IN_INDEX=0 (the only physically-wired input).
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
import numpy as np
from audio_io import DEFAULT_SR, mono_to_stereo, play_and_record, sine
from dsp408 import Device, enumerate_devices

SR = DEFAULT_SR
DSP_IN = 0   # Scarlett OUT 1 → DSP IN 1 (zero-indexed)
DSP_OUT = 1  # DSP OUT 2 → Scarlett IN 1 (zero-indexed)


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
    dsp.set_crossover(DSP_OUT, 10, 0, 0, 22000, 0, 0)  # wide-open
    for b in range(dsp.EQ_BAND_COUNT):
        dsp.set_eq_band(DSP_OUT, b, dsp.EQ_DEFAULT_FREQS_HZ[b], 0.0)
    # Reset input subsystem
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.3)


def rms_dbfs(x):
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    if rms < 1e-9: return -120.0
    return 20.0 * np.log10(rms * np.sqrt(2.0))


def measure(in_dbfs, freq=1000, dur=1.0):
    tone = sine(freq, dur, amp_dbfs=in_dbfs)
    cap = play_and_record(mono_to_stereo(tone, left=True, right=False))
    body = slice(int(0.3 * SR), len(cap.in1) - int(0.1 * SR))
    return rms_dbfs(cap.in1[body]), cap


def measure_phase(in_dbfs, freq=1000):
    """Return cross-correlation lag between source (lp1) and capture (in1)
    in samples. Used to detect polar flips (180° phase = correlation
    minimum)."""
    tone = sine(freq, 1.0, amp_dbfs=in_dbfs)
    cap = play_and_record(mono_to_stereo(tone, left=True, right=False))
    body = slice(int(0.3 * SR), len(cap.in1) - int(0.1 * SR))
    src = cap.lp1[body]
    rec = cap.in1[body]
    # Cross-correlation peak — find lag and sign
    n = min(len(src), len(rec), 4096)
    src_n = src[:n] - np.mean(src[:n])
    rec_n = rec[:n] - np.mean(rec[:n])
    src_n /= (np.sqrt(np.mean(src_n ** 2)) + 1e-9)
    rec_n /= (np.sqrt(np.mean(rec_n ** 2)) + 1e-9)
    lags = np.arange(-100, 101)
    corr = []
    for lag in lags:
        if lag >= 0:
            c = np.mean(src_n[:n - lag] * rec_n[lag:n])
        else:
            c = np.mean(src_n[-lag:n] * rec_n[:n + lag])
        corr.append(c)
    corr = np.array(corr)
    peak_lag = lags[np.argmax(np.abs(corr))]
    peak_corr = corr[np.argmax(np.abs(corr))]
    return float(peak_corr), int(peak_lag)


info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    setup(dsp)

    # ─────────────────────────────────────────────────────────────────
    # TEST 1: INPUT MUTE — does muted=True silence the audio?
    # ─────────────────────────────────────────────────────────────────
    print("=== TEST 1: INPUT MUTE (in MISC byte 3) ===")
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.3)
    out_unmuted, _ = measure(in_dbfs=-10)
    print(f"  unmuted: {out_unmuted:+.2f} dBFS")
    dsp.set_input(DSP_IN, polar=False, muted=True, delay_samples=0, volume=0)
    time.sleep(0.3)
    out_muted, _ = measure(in_dbfs=-10)
    print(f"  muted:   {out_muted:+.2f} dBFS")
    diff = out_muted - out_unmuted
    if diff < -40:
        print(f"  ✅ INPUT MUTE WORKS — {diff:.1f} dB attenuation")
    elif diff < -10:
        print(f"  ⚠ partial mute — {diff:.1f} dB attenuation (may not be true mute)")
    else:
        print(f"  ❌ INPUT MUTE NO-OP — only {diff:.1f} dB diff")
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 2: INPUT POLAR — does polar=True invert phase?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 2: INPUT POLAR (in MISC byte 1) ===")
    # Make sure output polar is OFF
    dsp.set_channel(DSP_OUT, db=0.0, muted=False, polar=False)
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.3)
    corr_normal, lag_normal = measure_phase(in_dbfs=-10)
    print(f"  polar=False: corr={corr_normal:+.3f} lag={lag_normal}")
    dsp.set_input(DSP_IN, polar=True, muted=False, delay_samples=0, volume=0)
    time.sleep(0.3)
    corr_invert, lag_invert = measure_phase(in_dbfs=-10)
    print(f"  polar=True:  corr={corr_invert:+.3f} lag={lag_invert}")
    if corr_normal > 0.5 and corr_invert < -0.5:
        print(f"  ✅ INPUT POLAR FLIPS PHASE — sign of correlation reversed")
    elif abs(corr_normal - corr_invert) > 0.5:
        print(f"  ⚠ partial polarity change")
    else:
        print(f"  ❌ INPUT POLAR NO-OP — correlation didn't flip sign")
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 3: INPUT DELAY — does delay shift the signal in time?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 3: INPUT DELAY (in MISC bytes 4..5) ===")
    for d in (0, 50, 100, 200):
        dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=d, volume=0)
        time.sleep(0.3)
        _, lag = measure_phase(in_dbfs=-10)
        print(f"  delay_samples={d:>4} → measured lag={lag} samples")
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 4: INPUT VOLUME — does the u8 attenuate, and how?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 4: INPUT VOLUME (in MISC byte 6) ===")
    base_out, _ = measure(in_dbfs=-10)
    print(f"  baseline (vol=0): {base_out:+.2f} dBFS")
    print(f"  vol(u8) | out (dBFS) | diff vs base")
    for vol in (0, 50, 100, 150, 200, 255):
        dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=vol)
        time.sleep(0.3)
        out, _ = measure(in_dbfs=-10)
        print(f"  {vol:>5}   | {out:+6.2f}    | {out - base_out:+.2f}")
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 5: INPUT EQ BAND — does +12 dB peak at 1 kHz produce a peak?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 5: INPUT EQ BAND 5 +12 dB @ 1 kHz ===")
    # Baseline at 500/1k/2k Hz
    base_levels = {}
    for f in (500, 1000, 2000):
        out, _ = measure(in_dbfs=-20, freq=f)
        base_levels[f] = out
    # Apply input EQ: band 5 = 1 kHz (output convention; input may differ)
    # Try band 0..14 to find which one is at 1 kHz on inputs.
    # Per probe earlier, input EQ defaults are 1/3-octave (20, 25, 32, 40,
    # 50, 63, 80, 100, 125...). 1000 Hz isn't a default center, but we can
    # set freq_hz=1000 explicitly.
    # Use band 5 (avoids MISC=9 / unknown=10 / noisegate=11 collision).
    try:
        dsp.set_input_eq_band(DSP_IN, band=5, freq_hz=1000, gain_db=+12, q=4.5)
        time.sleep(0.4)
        out_levels = {}
        for f in (500, 1000, 2000):
            out, _ = measure(in_dbfs=-20, freq=f)
            out_levels[f] = out
        print(f"  freq | base    | with EQ | diff")
        for f in (500, 1000, 2000):
            d = out_levels[f] - base_levels[f]
            print(f"  {f:>4} | {base_levels[f]:+6.2f} | {out_levels[f]:+6.2f} | {d:+.2f} dB")
        peak_diff_1k = out_levels[1000] - base_levels[1000]
        peak_diff_500 = out_levels[500] - base_levels[500]
        if peak_diff_1k > 8 and peak_diff_500 < 2:
            print(f"  ✅ INPUT EQ ACTIVE — +12 dB requested, +{peak_diff_1k:.1f} dB measured at 1 kHz")
        elif peak_diff_1k > 2:
            print(f"  ⚠ INPUT EQ PARTIAL — only +{peak_diff_1k:.1f} dB at 1 kHz")
        else:
            print(f"  ❌ INPUT EQ INERT — only {peak_diff_1k:+.2f} dB at 1 kHz")
        # Reset
        dsp.set_input_eq_band(DSP_IN, band=5, freq_hz=63, gain_db=0, q=4.5)
    except Exception as e:
        print(f"  ERROR: {e}")
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 6: INPUT NOISEGATE — does it gate below threshold?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 6: INPUT NOISEGATE (DataID=11) ===")
    # Quiet input first
    out_quiet, _ = measure(in_dbfs=-50)
    out_loud, _ = measure(in_dbfs=-3)
    print(f"  baseline -50 dBFS in: out={out_quiet:+.2f} dBFS")
    print(f"  baseline -3 dBFS in:  out={out_loud:+.2f} dBFS")
    # Set high threshold — should gate quiet signals
    dsp.set_input_noisegate(DSP_IN, threshold=200, attack=10, knee=10,
                             release=10, config=0xFF)
    time.sleep(0.4)
    out_quiet_g, _ = measure(in_dbfs=-50)
    out_loud_g, _ = measure(in_dbfs=-3)
    print(f"  with gate (thr=200): -50 dBFS in → out={out_quiet_g:+.2f} dBFS  "
          f"(diff {out_quiet_g - out_quiet:+.2f})")
    print(f"  with gate (thr=200):  -3 dBFS in → out={out_loud_g:+.2f} dBFS  "
          f"(diff {out_loud_g - out_loud:+.2f})")
    if out_quiet_g - out_quiet < -10:
        print(f"  ✅ NOISEGATE WORKS — quiet signal attenuated")
    else:
        print(f"  ❌ NOISEGATE NO-OP")
    dsp.set_input_noisegate(DSP_IN, 0, 0, 0, 0, 0)
    time.sleep(0.2)

    # ─────────────────────────────────────────────────────────────────
    # TEST 7: BONUS — set_full_channel_state polar bit AUDIBLY?
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 7: set_full_channel_state polar AUDIBLY ===")
    blob_in = bytes(dsp.read_channel_state(DSP_OUT))
    # Confirm baseline polar via measurement
    corr_base, _ = measure_phase(in_dbfs=-10)
    print(f"  baseline corr={corr_base:+.3f}")
    # Flip polar via full-state write
    blob_mod = bytearray(blob_in)
    blob_mod[247] = 0 if blob_mod[247] else 1
    dsp.set_full_channel_state(DSP_OUT, bytes(blob_mod))
    time.sleep(0.4)
    corr_flipped, _ = measure_phase(in_dbfs=-10)
    print(f"  after full-state-write polar flip: corr={corr_flipped:+.3f}")
    if corr_base * corr_flipped < 0:
        print(f"  ✅ multi-frame WRITE delivered polar bit AUDIBLY")
    else:
        print(f"  ❌ polar didn't flip via full-state write (or 2-byte loss got it)")
    # Restore
    dsp.set_full_channel_state(DSP_OUT, blob_in)
    time.sleep(0.4)

    # ─────────────────────────────────────────────────────────────────
    # TEST 8: BONUS — apply_speaker_template audible effect
    # ─────────────────────────────────────────────────────────────────
    print("\n=== TEST 8: apply_speaker_template AUDIBLE effect ===")
    # Measure baseline at 1 kHz
    out_base, _ = measure(in_dbfs=-20, freq=1000)
    out_high, _ = measure(in_dbfs=-20, freq=10000)
    out_low, _ = measure(in_dbfs=-20, freq=100)
    print(f"  baseline (current spk_type): "
          f"100Hz={out_low:+.1f}  1kHz={out_base:+.1f}  10kHz={out_high:+.1f}")
    for tmpl in ("sub", "fl_tweeter", "fl"):
        try:
            dsp.apply_speaker_template(DSP_OUT, tmpl)
            time.sleep(0.4)
            o_low, _ = measure(in_dbfs=-20, freq=100)
            o_mid, _ = measure(in_dbfs=-20, freq=1000)
            o_high, _ = measure(in_dbfs=-20, freq=10000)
            print(f"  {tmpl:>10}: 100={o_low:+.1f}  1k={o_mid:+.1f}  "
                  f"10k={o_high:+.1f}  → "
                  f"Δ100={o_low-out_low:+.1f}  Δ10k={o_high-out_high:+.1f}")
        except Exception as e:
            print(f"  {tmpl:>10}: ERROR {e}")

    # Cleanup
    dsp.apply_speaker_template(DSP_OUT, "fl")  # neutral default
    for ch in range(8):
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
        dsp.set_channel(ch, db=0.0, muted=False)
    dsp.set_input(DSP_IN, polar=False, muted=False, delay_samples=0, volume=0)
    print("\n=== ALL TESTS DONE ===")

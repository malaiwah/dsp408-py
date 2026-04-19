"""Compressor behavior probe — pin down threshold units, enable flag,
attack/release time constants, all_pass_q semantics.

Strategy:
  1. Establish a clean baseline: play -3 dBFS tone with compressor disabled
     (enable=0, threshold=255), measure output level. That's "no compression".
  2. Enable=0/1 toggle: same input, vary just enable. If the readback level
     differs, the enable flag works as expected.
  3. Threshold sweep: enable=1, walk threshold 255 → 0 in steps. Plot input
     level (constant) vs output level. Find where compression starts (knee)
     and what slope the gain reduction follows.
  4. Attack/Release: step input from -30 dBFS to -3 dBFS abruptly with
     compressor active and threshold low; measure envelope time constant
     for the level to settle (attack). Reverse step for release.
  5. all_pass_q sanity: hold all else fixed, vary q ∈ {1, 100, 420, 5000}
     and see if anything changes.

Output: mostly numeric — print level diff per condition. We want to show
*compression actually happens* first, then refine.
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
import numpy as np
from audio_io import DEFAULT_SR, mono_to_stereo, play_and_record, sine
from dsp408 import Device, enumerate_devices

SR = DEFAULT_SR
DSP_OUT = 1  # the wired DSP output → Scarlett IN 1


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
    # Wide-open crossover, flat EQ — isolate the compressor's effect.
    dsp.set_crossover(DSP_OUT, 10, 0, 0, 22000, 0, 0)
    for b in range(dsp.EQ_BAND_COUNT):
        dsp.set_eq_band(DSP_OUT, b, dsp.EQ_DEFAULT_FREQS_HZ[b], 0.0)
        time.sleep(0.02)
    time.sleep(0.3)


def rms_dbfs(x):
    """Return RMS in dBFS (0 dBFS = full-scale sine peak amplitude 1.0)."""
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    if rms < 1e-9: return -120.0
    # For a sine, peak = sqrt(2)*RMS, so dBFS_peak = 20*log10(rms*sqrt(2))
    return 20.0 * np.log10(rms * np.sqrt(2.0))


def measure_steady(dsp, in_dbfs, dur=1.5, freq=1000):
    """Play a steady tone, return (in_dbfs_measured, out_dbfs_measured)."""
    tone = sine(freq, dur, amp_dbfs=in_dbfs)
    cap = play_and_record(mono_to_stereo(tone, left=True, right=False))
    # Skip startup transient + tail
    body = slice(int(0.3 * SR), len(cap.in1) - int(0.1 * SR))
    return rms_dbfs(cap.lp1[body]), rms_dbfs(cap.in1[body])


def measure_step(dsp, low_dbfs, high_dbfs, hold=0.5, freq=1000):
    """Play [silence][tone at high_dbfs][silence], return time-domain capture
    for envelope analysis. Pre-roll low_dbfs lets us capture attack.
    """
    n_low = int(0.5 * SR)
    n_high = int(hold * SR)
    n_off = int(0.5 * SR)
    pre = sine(freq, 0.5, amp_dbfs=low_dbfs)
    hi  = sine(freq, hold, amp_dbfs=high_dbfs)
    off = np.zeros(n_off, dtype=np.float32)
    sig = np.concatenate([pre, hi, off])[: n_low + n_high + n_off]
    cap = play_and_record(mono_to_stereo(sig, left=True, right=False))
    return cap


def envelope(x, win_ms=2.0, sr=SR):
    """Sliding-window RMS envelope in dBFS, useful for attack/release fitting."""
    win = int(win_ms / 1000.0 * sr)
    win = max(win, 1)
    sq = x.astype(np.float64) ** 2
    cs = np.concatenate(([0.0], np.cumsum(sq)))
    rms = np.sqrt((cs[win:] - cs[:-win]) / win)
    rms = np.maximum(rms, 1e-9)
    return 20.0 * np.log10(rms * np.sqrt(2.0))


def fit_time_const(env, sr, idx_start, target_db, polarity="down"):
    """Crude time-const fit: time from idx_start until env reaches
    63% of the way from start_db → target_db. Returns ms."""
    if idx_start >= len(env): return float("nan")
    start_db = env[idx_start]
    if polarity == "down":
        # going DOWN to target_db: 63% point
        sixty_three = start_db - 0.63 * (start_db - target_db)
        for i in range(idx_start, len(env)):
            if env[i] <= sixty_three: return (i - idx_start) / sr * 1000.0
    else:
        sixty_three = start_db + 0.63 * (target_db - start_db)
        for i in range(idx_start, len(env)):
            if env[i] >= sixty_three: return (i - idx_start) / sr * 1000.0
    return float("nan")


info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    setup(dsp)

    # === Test 1: baseline (compressor effectively off) ===
    print("=== TEST 1: baseline — compressor disabled (enable=0, thr=255) ===")
    dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                       threshold=255, all_pass_q=420, enable=False)
    time.sleep(0.4)
    base_outs = {}
    for in_db in (-30, -20, -10, -6, -3):
        out_lp, out_in = measure_steady(dsp, in_db)
        base_outs[in_db] = (out_lp, out_in)
        print(f"  in={in_db:>+4} dBFS → loopback={out_lp:+6.2f} dBFS, "
              f"DSP→Scarlett={out_in:+6.2f} dBFS  (gain {out_in - out_lp:+.2f} dB)")

    # === Test 2: enable=0/1 toggle at -3 dBFS, low threshold ===
    print("\n=== TEST 2: enable flag toggle (in=-3 dBFS, thr=0) ===")
    for en in (False, True, False, True):
        dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                           threshold=0, all_pass_q=420, enable=en)
        time.sleep(0.5)
        out_lp, out_in = measure_steady(dsp, -3)
        print(f"  enable={int(en)} → DSP→Scarlett={out_in:+6.2f} dBFS  "
              f"(diff vs base {out_in - base_outs[-3][1]:+.2f} dB)")

    # === Test 3: threshold sweep ===
    print("\n=== TEST 3: threshold sweep (enable=1, in=-3 dBFS) ===")
    print("  Looking for: (a) does output drop as threshold falls? "
          "(b) what raw value first causes compression?")
    for thr in (255, 200, 150, 100, 80, 60, 40, 20, 10, 0):
        dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                           threshold=thr, all_pass_q=420, enable=True)
        time.sleep(0.4)
        out_lp, out_in = measure_steady(dsp, -3)
        diff = out_in - base_outs[-3][1]
        bar = "▼" * max(0, int(-diff))
        print(f"  thr={thr:>3} → out={out_in:+6.2f} dBFS  "
              f"(diff {diff:+5.2f} dB) {bar}")

    # === Test 4: input-level sweep at fixed threshold (find knee) ===
    print("\n=== TEST 4: input sweep at thr=80 (find compression knee) ===")
    dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                       threshold=80, all_pass_q=420, enable=True)
    time.sleep(0.4)
    for in_db in (-30, -20, -15, -10, -6, -3, 0):
        out_lp, out_in = measure_steady(dsp, in_db)
        # Compare against the baseline output for the SAME input level
        if in_db in base_outs:
            base_out_in = base_outs[in_db][1]
            diff = out_in - base_out_in
            print(f"  in={in_db:>+4} dBFS → out={out_in:+6.2f} dBFS  "
                  f"(vs base {base_out_in:+6.2f}, diff {diff:+.2f} dB)")
        else:
            print(f"  in={in_db:>+4} dBFS → out={out_in:+6.2f} dBFS")

    # === Test 5: attack/release envelope fit ===
    print("\n=== TEST 5: attack/release envelope fit (in step -30→-3 dBFS) ===")
    for atk, rel in [(10, 100), (56, 500), (200, 1000), (500, 2000)]:
        dsp.set_compressor(channel=DSP_OUT, attack_ms=atk, release_ms=rel,
                           threshold=80, all_pass_q=420, enable=True)
        time.sleep(0.4)
        cap = measure_step(dsp, low_dbfs=-30, high_dbfs=-3)
        env = envelope(cap.in1, win_ms=2.0)
        # Find the step transition (input goes from quiet to loud at sample 0.5*SR)
        i_attack_start = int(0.5 * SR)
        # First find steady-state low and high envelope
        steady_low = float(np.median(env[max(0, i_attack_start - int(0.1*SR)):i_attack_start]))
        steady_high = float(np.median(env[i_attack_start + int(0.4*SR):i_attack_start + int(0.45*SR)]))
        # Attack: 63% from steady_low → steady_high
        atk_meas = fit_time_const(env, SR, i_attack_start,
                                   target_db=steady_high, polarity="up")
        # Release: at end of tone, signal cuts to silence
        i_release_start = int((0.5 + 0.5) * SR)  # 0.5s pre + 0.5s hold
        rel_meas = fit_time_const(env, SR, i_release_start,
                                   target_db=steady_low, polarity="down")
        print(f"  atk={atk:>3}ms rel={rel:>4}ms  →  "
              f"measured atk≈{atk_meas:.1f}ms  rel≈{rel_meas:.1f}ms  "
              f"(low={steady_low:.1f}, high={steady_high:.1f} dBFS)")

    # === Test 6: all_pass_q sanity ===
    print("\n=== TEST 6: all_pass_q sweep (in=-3 dBFS, thr=80, atk/rel=56/500) ===")
    for q in (1, 100, 420, 1000, 5000, 65535):
        dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                           threshold=80, all_pass_q=q, enable=True)
        time.sleep(0.4)
        out_lp, out_in = measure_steady(dsp, -3)
        diff = out_in - base_outs[-3][1]
        print(f"  q={q:>5} → out={out_in:+6.2f} dBFS  (diff vs base {diff:+.2f} dB)")

    # Restore
    dsp.set_compressor(channel=DSP_OUT, attack_ms=56, release_ms=500,
                       threshold=0, all_pass_q=420, enable=False)
    for ch in range(8):
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
        dsp.set_channel(ch, db=0.0, muted=False)
    print("\n=== DONE ===")

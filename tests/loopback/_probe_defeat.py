"""Settle whether filter_type=3 is actually 'bypass' or something else.

Hypotheses:
  A) type=3 → firmware truly bypasses filter (any freq/slope irrelevant)
  B) type=3 → firmware does SOMETHING ELSE that happens to look bypass-y
     in some configs (e.g., interprets type=3 as a degenerate filter coeff)

Distinguishing test: with type=3, vary freq + slope. If output is invariant,
hypothesis A. If output changes with freq/slope, hypothesis B (and we now
know what the byte actually does).

Reference: type=0 / slope=8 (known bypass via the slope-byte mechanism).
"""
import sys, time
sys.path.insert(0, "/home/mbelleau/dsp408")
import numpy as np
from audio_io import DEFAULT_SR, mono_to_stereo, play_and_record
from dsp408 import Device, enumerate_devices

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
    for b in range(dsp.EQ_BAND_COUNT):
        dsp.set_eq_band(DSP_OUT, b, dsp.EQ_DEFAULT_FREQS_HZ[b], 0.0)
    time.sleep(0.3)


def pink(n, dbfs=-18.0, seed=0):
    rng = np.random.default_rng(seed)
    F = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1.0 / SR)
    sh = np.ones_like(f); sh[1:] = 1.0 / np.sqrt(f[1:])
    F *= sh
    x = np.fft.irfft(F, n=n); x /= np.max(np.abs(x))
    return (x * 10 ** (dbfs / 20)).astype(np.float32)


def welch(x, sr, nperseg=8192):
    win = np.hanning(nperseg); wn = (win * win).sum(); step = nperseg // 2
    nseg = 1 + (len(x) - nperseg) // step
    psd = np.zeros(nperseg // 2 + 1)
    for i in range(nseg):
        seg = x[i * step:i * step + nperseg] * win
        S = np.fft.rfft(seg); psd += (S.conj() * S).real
    psd /= (nseg * wn * sr); return np.fft.rfftfreq(nperseg, 1.0 / sr), psd


def measure(seed=42):
    cap = play_and_record(mono_to_stereo(pink(int(1.5 * SR), seed=seed),
                                          left=True, right=False))
    body = slice(int(0.10 * SR), len(cap.in1) - int(0.05 * SR))
    f, P = welch(cap.in1[body], cap.sr); _, R = welch(cap.lp1[body], cap.sr)
    return f, 10 * np.log10((P + 1e-20) / (R + 1e-20))


def gain_at(f, H, fc, oct=0.25):
    """Return mean gain in a +/-1/4-octave window around fc."""
    m = (f >= fc / 2 ** oct) & (f <= fc * 2 ** oct)
    return float(np.mean(H[m]))


info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    setup(dsp)

    # === Reference: type=0 (BW) / slope=8 (Off) — known good bypass ===
    print("=== REFERENCE: HPF bypass via slope=8 (BW) + LPF bypass via slope=8 ===")
    dsp.set_crossover(DSP_OUT, 20, 0, 8, 20000, 0, 8); time.sleep(0.3)
    f0, H_ref = measure()
    g100 = gain_at(f0, H_ref, 100)
    g1k  = gain_at(f0, H_ref, 1000)
    g10k = gain_at(f0, H_ref, 10000)
    print(f"  reference  100Hz={g100:+.2f}  1kHz={g1k:+.2f}  10kHz={g10k:+.2f}  dB")

    # Helper: measure a config + report deviation vs reference at 3 freqs
    def run(label, hpf_args, lpf_args):
        hf, ht, hs = hpf_args
        lf, lt, ls = lpf_args
        dsp.set_crossover(DSP_OUT, hf, ht, hs, lf, lt, ls); time.sleep(0.3)
        f, H = measure()
        d100 = gain_at(f, H, 100) - g100
        d1k  = gain_at(f, H, 1000) - g1k
        d10k = gain_at(f, H, 10000) - g10k
        # Flag: |Δ| < 0.5 dB at all 3 = bypass; >2 dB at any = filter active
        bypass = abs(d100) < 0.5 and abs(d1k) < 0.5 and abs(d10k) < 0.5
        active = abs(d100) > 2 or abs(d1k) > 2 or abs(d10k) > 2
        flag = "BYPASS" if bypass else ("ACTIVE" if active else "?")
        print(f"  {label:<40}  Δ100={d100:+.2f}  Δ1k={d1k:+.2f}  "
              f"Δ10k={d10k:+.2f}  → {flag}")
        return f, H

    # === LPF: vary type=3 with various freq + slope ===
    print("\n=== LPF: type=3 (Defeat?) — vary freq + slope ===")
    print("  HPF held at bypass (type=0, slope=8)")
    print("  If type=3 is true bypass, ALL of these should match reference")
    print("  If type=3 is something else, freq/slope should change the response")
    for lf, ls in [(20000, 3), (2000, 3), (200, 3), (2000, 0),
                    (2000, 1), (2000, 5), (2000, 7), (2000, 8)]:
        run(f"LPF type=3 freq={lf:>5} slope={ls}",
            (20, 0, 8), (lf, 3, ls))

    # === HPF: vary type=3 with various freq + slope ===
    print("\n=== HPF: type=3 (Defeat?) — vary freq + slope ===")
    print("  LPF held at bypass (type=0, slope=8)")
    for hf, hs in [(20, 3), (200, 3), (2000, 3), (20, 0),
                    (20, 1), (20, 5), (20, 7), (20, 8)]:
        run(f"HPF type=3 freq={hf:>5} slope={hs}",
            (hf, 3, hs), (20000, 0, 8))

    # === Sanity check: KNOWN-ACTIVE configurations should NOT be bypass ===
    print("\n=== SANITY: known active filters should show ACTIVE ===")
    run("LPF BW 24 @ 2000 Hz", (20, 0, 8), (2000, 0, 3))
    run("HPF BW 24 @ 200 Hz",  (200, 0, 3), (20000, 0, 8))

    # === The smoking gun: type=3 with slope=0 (6 dB/oct) at LOW freq ===
    # If this is real bypass, response is flat. If type=3 only LOOKS like
    # bypass at type=0/slope=high-but-not-8, then mid-spectrum could move.
    print("\n=== SMOKING GUN: type=3 with slope=0 (lowest) at fc=200 ===")
    run("LPF type=3 slope=0 fc=200 (mild filter if active)",
        (20, 0, 8), (200, 3, 0))
    run("HPF type=3 slope=0 fc=2000 (mild filter if active)",
        (2000, 3, 0), (20000, 0, 8))

    # Restore
    dsp.set_crossover(DSP_OUT, 20, 0, 8, 20000, 0, 8)
    for ch in range(8):
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
        dsp.set_channel(ch, db=0.0, muted=False)
    print("\n=== DONE ===")

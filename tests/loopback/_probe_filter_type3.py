"""Compare filter_type=0/1/2/3 at same freq+slope to identify what type 3 IS."""
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


def gain_at(f, H, fc, oct=0.20):
    m = (f >= fc / 2 ** oct) & (f <= fc * 2 ** oct)
    return float(np.mean(H[m]))


info = enumerate_devices()[0]
with Device.open(path=info["path"]) as dsp:
    dsp.connect()
    setup(dsp)

    # Reference: full bypass
    dsp.set_crossover(DSP_OUT, 20, 0, 8, 20000, 0, 8); time.sleep(0.3)
    f0, H_ref = measure()

    print("=== LPF @ 2000 Hz, slope=3 (24 dB/oct), filter type 0/1/2/3 ===")
    print("  Looking for: which type 3 matches (BW=0, Bessel=1, LR=2)?")
    print(f"  {'type':>4}  Δ@500  Δ@1k   Δ@1.4k Δ@2k    Δ@3k   Δ@5k   Δ@10k")
    rows = {}
    for t in (0, 1, 2, 3):
        dsp.set_crossover(DSP_OUT, 20, 0, 8, 2000, t, 3); time.sleep(0.3)
        f, H = measure()
        D = {fc: gain_at(f, H, fc) - gain_at(f0, H_ref, fc)
             for fc in (500, 1000, 1414, 2000, 3000, 5000, 10000)}
        rows[t] = D
        print(f"  {t:>4}  {D[500]:+.2f}  {D[1000]:+.2f}  {D[1414]:+.2f}  "
              f"{D[2000]:+.2f}  {D[3000]:+.2f}  {D[5000]:+.2f}  {D[10000]:+.2f}")

    # Pairwise diffs
    print("\n=== Type-3 vs others (point-by-point dB diff) ===")
    for ref_t in (0, 1, 2):
        max_diff = max(abs(rows[3][fc] - rows[ref_t][fc])
                       for fc in (500, 1000, 1414, 2000, 3000, 5000, 10000))
        print(f"  |type3 - type{ref_t}|_max = {max_diff:.2f} dB")
    print("  (< 0.5 dB → identical filter; > 1 dB → distinct alignment)")

    # Restore
    dsp.set_crossover(DSP_OUT, 20, 0, 8, 20000, 0, 8)
    for ch in range(8):
        dsp.set_routing(ch, in1=False, in2=False, in3=False, in4=False)
        dsp.set_channel(ch, db=0.0, muted=False)
    print("\n=== DONE ===")

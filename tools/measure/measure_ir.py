"""Capture one room impulse response using Scarlett 2i2 hardware loopback.

Plays a Farina log sweep on one Scarlett output, simultaneously captures all
four input channels, and deconvolves mic÷loopback in the spectral domain so
the resulting IR is in the reference frame of the actual electrical signal.
DAC / cable / DSP constants cancel across speakers, so peak times are
directly comparable for delay alignment.

Defaults assume Scarlett 2i2 4th gen:
  - Out 1   = sweep
  - in 1    = mic (uncalibrated is fine — we only need timing, not magnitude)
  - in 3    = hardware loopback of Out 1 (no jumper required on 4th gen)

Output is a .npz containing the IR, sample rate, sub-sample peak time
(parabolic interpolation), and peak/RMS levels for sanity checking.

CLI is compatible with iterate_all.py's --measure-script flag: --cal-file is
accepted as a no-op so the existing solo-cycle orchestrator can drive this
script unchanged.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import sounddevice as sd


def farina_log_sweep(n: int, fs: int, f0: float = 20.0, f1: float = 22000.0,
                     level_dbfs: float = -12.0) -> np.ndarray:
    """Exponential sine sweep, Hann-ramped at the edges to suppress clicks."""
    t = np.arange(n) / fs
    T = n / fs
    L = T / np.log(f1 / f0)
    K = 2 * np.pi * f0 * L
    sweep = np.sin(K * (np.exp(t / L) - 1.0))
    ramp = max(int(0.005 * fs), 16)
    sweep[:ramp] *= np.linspace(0.0, 1.0, ramp)
    sweep[-ramp:] *= np.linspace(1.0, 0.0, ramp)
    sweep *= 10 ** (level_dbfs / 20)
    return sweep.astype(np.float32)


def deconvolve(mic: np.ndarray, ref: np.ndarray,
               reg: float = 1e-3) -> np.ndarray:
    """Spectral division mic÷ref with Tikhonov regularization.

    H(f) = M(f) · conj(R(f)) / (|R(f)|² + ε · max|R|²)
    """
    n = max(len(mic), len(ref))
    n_fft = 1
    while n_fft < n:
        n_fft *= 2
    M = np.fft.rfft(mic, n_fft)
    R = np.fft.rfft(ref, n_fft)
    R_mag2 = np.abs(R) ** 2
    eps = reg * float(np.max(R_mag2))
    H = M * np.conj(R) / (R_mag2 + eps)
    return np.fft.irfft(H, n_fft)


def find_peak_subsample(ir: np.ndarray, fs: int,
                        search_ms: float = 50.0) -> tuple[float, int]:
    """Return (peak_time_seconds, integer_peak_sample). Parabolic interp."""
    n_search = int(search_ms * fs / 1000)
    win = np.abs(ir[:n_search])
    i = int(np.argmax(win))
    if 0 < i < len(win) - 1:
        ym, y0, yp = float(win[i-1]), float(win[i]), float(win[i+1])
        denom = ym - 2.0 * y0 + yp
        delta = 0.5 * (ym - yp) / denom if denom != 0.0 else 0.0
    else:
        delta = 0.0
    return (i + delta) / fs, i


def parse_device(s):
    if s is None:
        return None
    try:
        return int(s)
    except ValueError:
        return s


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--title", default="IR")
    ap.add_argument("--output", required=True,
                    help="Output path (extension normalized to .npz)")
    ap.add_argument("--sweep-length", type=int, default=131072,
                    help="Sweep samples (default 131072 ≈ 3 s @ 44.1k)")
    ap.add_argument("--samplerate", type=int, default=44100)
    ap.add_argument("--level-dbfs", type=float, default=-12.0)
    ap.add_argument("--silence-pad-s", type=float, default=1.0,
                    help="Silence appended after sweep (capture late energy)")
    ap.add_argument("--output-device", default=None,
                    help="sounddevice index/name for the Scarlett")
    ap.add_argument("--input-device", default=None,
                    help="Should match --output-device (same Scarlett)")
    ap.add_argument("--output-ch", type=int, default=0,
                    help="0=Out 1 (Left), 1=Out 2 (Right)")
    ap.add_argument("--mic-ch", type=int, default=0,
                    help="Input channel for mic (0 = in 1)")
    ap.add_argument("--ref-ch", type=int, default=2,
                    help="Input channel for loopback (2 = in 3 on 4th gen 2i2)")
    ap.add_argument("--search-ms", type=float, default=50.0,
                    help="Window after t=0 to search for direct-sound peak")
    ap.add_argument("--cal-file", default=None,
                    help="(Ignored — accepted for iterate_all.py compatibility)")
    args = ap.parse_args()

    fs = args.samplerate
    sweep = farina_log_sweep(args.sweep_length, fs, level_dbfs=args.level_dbfs)
    pad = np.zeros(int(args.silence_pad_s * fs), dtype=np.float32)
    play_mono = np.concatenate([sweep, pad])

    play = np.zeros((len(play_mono), 2), dtype=np.float32)
    if not 0 <= args.output_ch < 2:
        sys.exit("--output-ch must be 0 or 1")
    play[:, args.output_ch] = play_mono

    out_dev = parse_device(args.output_device)
    in_dev = parse_device(args.input_device)
    if in_dev is None and out_dev is None:
        device = None
    else:
        device = (in_dev if in_dev is not None else out_dev,
                  out_dev if out_dev is not None else in_dev)

    print(f"[{args.title}] sweep {args.sweep_length} samp + {len(pad)} pad "
          f"@ {fs} Hz   out_ch={args.output_ch}  mic={args.mic_ch}  ref={args.ref_ch}")
    rec = sd.playrec(play, samplerate=fs, channels=4,
                     device=device, dtype='float32')
    sd.wait()

    mic = rec[:, args.mic_ch].astype(np.float64)
    ref = rec[:, args.ref_ch].astype(np.float64)

    def db(x):
        return 20 * np.log10(max(float(x), 1e-12))

    mic_peak, mic_rms = float(np.max(np.abs(mic))), float(np.sqrt(np.mean(mic ** 2)))
    ref_peak, ref_rms = float(np.max(np.abs(ref))), float(np.sqrt(np.mean(ref ** 2)))
    print(f"  mic  peak {db(mic_peak):+6.1f} dBFS  rms {db(mic_rms):+6.1f} dBFS")
    print(f"  ref  peak {db(ref_peak):+6.1f} dBFS  rms {db(ref_rms):+6.1f} dBFS")
    if mic_peak > 0.99:
        print("  WARNING: mic clipped — back off Scarlett input gain.")
    if mic_peak < 0.005:
        print("  WARNING: mic very quiet — boost Scarlett gain or check connection.")
    if ref_peak < 0.01:
        print("  WARNING: loopback ref very quiet — confirm in 3 is loopback L "
              "on this 2i2 (Focusrite Control routing).")

    ir = deconvolve(mic, ref)
    peak_t, peak_i = find_peak_subsample(ir, fs, search_ms=args.search_ms)

    out_path = os.path.splitext(args.output)[0] + ".npz"
    np.savez_compressed(out_path,
                        ir=ir.astype(np.float32),
                        fs=fs,
                        peak_sample=peak_i,
                        peak_time_s=peak_t,
                        title=args.title,
                        mic_peak_dbfs=db(mic_peak),
                        mic_rms_dbfs=db(mic_rms),
                        ref_peak_dbfs=db(ref_peak),
                        ref_rms_dbfs=db(ref_rms))
    print(f"  IR peak: sample {peak_i}  →  {peak_t*1000:.3f} ms   "
          f"saved → {out_path}")


if __name__ == "__main__":
    main()

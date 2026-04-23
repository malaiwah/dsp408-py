"""Per-speaker time-of-flight from 4 IR captures → DSP-408 delay assignments.

Loads .npz files produced by measure_ir.py for FR/FL/RearR/RearL, finds the
relative time of flight using the sub-sample peak times, and emits the
per-channel delay needed to align all 4 speakers at the mic position.

The DSP-408 only adds delay (no negative). The farthest speaker is anchored at
delay=0; closer speakers get delay added so their direct sound arrives at the
same time at the mic. Distances (343 m/s) reported alongside as a reality check.

Usage:
    python compute_delays.py --prefix P5_IR
"""
import argparse
import os

import numpy as np


SPEED_OF_SOUND_M_S = 343.0  # @ ~20 °C
LABEL_TO_DSP_CH = {"FR": 0, "FL": 1, "RearR": 6, "RearL": 7}


def load_one(path):
    z = np.load(path, allow_pickle=True)
    return {
        "path": path,
        "peak_time_s": float(z["peak_time_s"]),
        "peak_sample": int(z["peak_sample"]),
        "fs": int(z["fs"]),
        "mic_peak_dbfs": float(z["mic_peak_dbfs"]) if "mic_peak_dbfs" in z.files else float("nan"),
    }


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", required=True,
                    help="Looks for {prefix}_{fr,fl,rear_r,rear_l}.npz")
    ap.add_argument("--dsp-fs", type=int, default=48000,
                    help="DSP-408 internal sample rate for delay-sample conversion. "
                         "Default 48000. Verify with `dev.read_channel(0)` if unsure.")
    ap.add_argument("--max-dsp-samples", type=int, default=1023,
                    help="Hard ceiling on DSP delay per channel")
    ap.add_argument("--speakers", default=None,
                    help="Override 'label:suffix' comma-separated "
                         "(default: FR/FL/RearR/RearL)")
    args = ap.parse_args()

    if args.speakers:
        spk = [(p.split(":")[0], f"{args.prefix}_{p.split(':')[1]}.npz")
               for p in args.speakers.split(",")]
    else:
        spk = [
            ("FR",    f"{args.prefix}_fr.npz"),
            ("FL",    f"{args.prefix}_fl.npz"),
            ("RearR", f"{args.prefix}_rear_r.npz"),
            ("RearL", f"{args.prefix}_rear_l.npz"),
        ]

    rows = []
    for label, path in spk:
        if not os.path.exists(path):
            print(f"  SKIP {label}: not found ({path})")
            continue
        rows.append((label, load_one(path)))

    if len(rows) < 2:
        print("Need at least 2 IR captures to compute relative delays.")
        return

    fs_capture = rows[0][1]["fs"]
    times = [r[1]["peak_time_s"] for r in rows]
    farthest_t = max(times)
    farthest_label = rows[int(np.argmax(times))][0]

    print(f"\nIR peak times (capture fs = {fs_capture} Hz, sub-sample interp):")
    print(f"{'Speaker':<8}  {'TOF (ms)':>10}  {'rel (ms)':>10}  "
          f"{'rel dist':>10}  {'DSP delay':>10}")
    print(f"{'':8}  {'':10}  {'':10}  {'(@343m/s)':>10}  "
          f"(@{args.dsp_fs} Hz)")
    print("-" * 64)

    snippets = []
    for label, d in rows:
        t = d["peak_time_s"]
        rel_s = farthest_t - t
        rel_ms = rel_s * 1000.0
        rel_cm = rel_s * SPEED_OF_SOUND_M_S * 100.0
        dsp_n = int(round(rel_s * args.dsp_fs))
        warn = ""
        if dsp_n > args.max_dsp_samples:
            warn = f"  CAPPED (was {dsp_n})"
            dsp_n = args.max_dsp_samples
        marker = "  ← reference (farthest)" if label == farthest_label else ""
        print(f"{label:<8}  {t*1000:>10.3f}  {rel_ms:>10.3f}  "
              f"{rel_cm:>8.1f} cm  {dsp_n:>10d}{marker}{warn}")
        snippets.append((label, dsp_n))

    print()
    print(f"Farthest speaker (delay=0): {farthest_label}")
    print(f"Max correction needed: "
          f"{(max(s for _, s in snippets) / args.dsp_fs)*1000:.2f} ms")
    print()
    print("=== dsp408-py snippet ===")
    print("# Apply per-channel delay to align direct sound at the mic position.")
    print("from dsp408 import Dsp408")
    print()
    print("dev = Dsp408()")
    print("dev.connect()  # required so firmware warmup runs before writes")
    for label, n in snippets:
        ch = LABEL_TO_DSP_CH.get(label, "?")
        print(f"dev.set_channel({ch}, delay_samples={n})  # {label}")
    print()
    print("# Re-run measure_ir.py + compute_delays.py to verify residual TOF spread")
    print("# is sub-sample (≪ 0.1 ms) after applying.")


if __name__ == "__main__":
    main()

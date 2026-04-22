#!/usr/bin/env python3
"""dsp408_blob_export.py — dump every reassembled 296-byte channel-state
blob from a pcapng as annotated JSON (one line per blob).

Uses tshark + the dsp408.lua dissector to get frame-level metadata, then
decodes each reassembled payload locally against dsp408/protocol.py so
the output is structured (not just hex).

Usage:
    tools/wireshark/dsp408_blob_export.py CAPTURE.pcapng [-o OUTFILE.jsonl]

Pipe it into jq / diff / sort to chase "what did the GUI write when I
clicked X?" questions:

    tools/wireshark/dsp408_blob_export.py cap.pcapng | jq 'select(.channel==2).basic'
    diff <(tools/wireshark/dsp408_blob_export.py a.pcapng) \\
         <(tools/wireshark/dsp408_blob_export.py b.pcapng)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

# Resolve tools/wireshark/dsp408.lua + project root so we can use protocol.py
THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent.parent
DISSECTOR = THIS_DIR / "dsp408.lua"

sys.path.insert(0, str(REPO_ROOT))
try:
    from dsp408 import protocol as P  # type: ignore
except ImportError:
    print(f"ERROR: cannot import dsp408.protocol from {REPO_ROOT}", file=sys.stderr)
    sys.exit(2)

SLOPE_NAMES = P.SLOPE_NAMES
FILTER_TYPE_NAMES = P.FILTER_TYPE_NAMES
SPK_TYPE_NAMES = P.SPK_TYPE_NAMES


def u16_le(buf: bytes, off: int) -> int:
    return buf[off] | (buf[off + 1] << 8)


def decode_blob(blob: bytes) -> dict:
    """Decode a 296-byte channel-state blob into structured JSON."""
    if len(blob) != P.BLOB_SIZE:
        return {"_error": f"expected {P.BLOB_SIZE} bytes, got {len(blob)}"}

    # EQ bands (10 × 8 bytes)
    bands = []
    for b in range(P.EQ_BAND_COUNT):
        off = b * 8
        freq = u16_le(blob, off)
        gain_raw = u16_le(blob, off + 2)
        bw = blob[off + 4]
        bands.append({
            "freq_hz": freq,
            "gain_db": (gain_raw - P.CHANNEL_VOL_OFFSET) / 10.0,
            "gain_raw": gain_raw,
            "bw_byte": bw,
            "q": round(P.EQ_Q_BW_CONSTANT / bw, 3) if bw else None,
        })

    # Basic record
    mute_flag = blob[P.OFF_MUTE]  # polarity INVERTED: 1=audible, 0=muted
    polar = blob[P.OFF_POLAR]
    gain_raw = u16_le(blob, P.OFF_GAIN)
    delay = u16_le(blob, P.OFF_DELAY)
    byte_252 = blob[P.OFF_BYTE_252]
    spk_type = blob[P.OFF_SPK_TYPE]

    # Crossover
    hpf_f = u16_le(blob, P.OFF_HPF_FREQ)
    hpf_t = blob[P.OFF_HPF_FILTER]
    hpf_s = blob[P.OFF_HPF_SLOPE]
    lpf_f = u16_le(blob, P.OFF_LPF_FREQ)
    lpf_t = blob[P.OFF_LPF_FILTER]
    lpf_s = blob[P.OFF_LPF_SLOPE]

    # Mixer
    mixer = list(blob[P.OFF_MIXER : P.OFF_MIXER + P.MIXER_CELLS])

    # Compressor (live record at 278..285, not the shadow at 270..277)
    comp_q = u16_le(blob, P.OFF_ALL_PASS_Q)
    comp_attack = u16_le(blob, P.OFF_ATTACK_MS)
    comp_release = u16_le(blob, P.OFF_RELEASE_MS)
    comp_thresh = blob[P.OFF_THRESHOLD]
    comp_link = blob[P.OFF_LINKGROUP]

    # Name
    name_bytes = blob[P.OFF_NAME : P.OFF_NAME + P.NAME_LEN]
    name = name_bytes.rstrip(b"\x00 ").decode("ascii", errors="replace")

    return {
        "eq_bands": bands,
        "basic": {
            "mute": mute_flag == 0,  # INVERTED
            "polar_inverted": polar != 0,
            "vol_db": (gain_raw - P.CHANNEL_VOL_OFFSET) / 10.0,
            "vol_raw": gain_raw,
            "delay_samples": delay,
            "byte_252": byte_252,
            "spk_type": spk_type,
            "spk_type_name": SPK_TYPE_NAMES[spk_type] if spk_type < len(SPK_TYPE_NAMES) else None,
        },
        "crossover": {
            "hpf": {
                "freq_hz": hpf_f,
                "filter": FILTER_TYPE_NAMES[hpf_t] if hpf_t < len(FILTER_TYPE_NAMES) else f"type_{hpf_t}",
                "slope": SLOPE_NAMES[hpf_s] if hpf_s < len(SLOPE_NAMES) else f"slope_{hpf_s}",
                "filter_raw": hpf_t,
                "slope_raw": hpf_s,
            },
            "lpf": {
                "freq_hz": lpf_f,
                "filter": FILTER_TYPE_NAMES[lpf_t] if lpf_t < len(FILTER_TYPE_NAMES) else f"type_{lpf_t}",
                "slope": SLOPE_NAMES[lpf_s] if lpf_s < len(SLOPE_NAMES) else f"slope_{lpf_s}",
                "filter_raw": lpf_t,
                "slope_raw": lpf_s,
            },
        },
        "mixer": mixer,
        "compressor": {
            "all_pass_q": comp_q,
            "attack_ms": comp_attack,
            "release_ms": comp_release,
            "threshold": comp_thresh,
            "linkgroup": comp_link,
        },
        "name": name,
    }


def run_tshark(pcap: Path) -> list[dict]:
    """Run tshark with our dissector and return one record per reassembled blob."""
    if not DISSECTOR.exists():
        print(f"ERROR: {DISSECTOR} not found", file=sys.stderr)
        sys.exit(2)
    cmd = [
        "tshark",
        "-X", f"lua_script:{DISSECTOR}",
        "-r", str(pcap),
        "-Y", "dsp408.reassembled_len == 296",
        "-T", "fields",
        "-e", "frame.number",
        "-e", "frame.time_epoch",
        "-e", "dsp408.cmd",
        "-e", "dsp408.cmd_name",
        "-e", "dsp408.direction",
        "-e", "dsp408.continuation_of",
        "-e", "dsp408.reassembled",
        "-E", "separator=|",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr, file=sys.stderr)
        sys.exit(proc.returncode)

    records = []
    for line in proc.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        frame_no, ts, cmd, cmd_name, direction, cont_of, hex_bytes = parts[:7]
        # tshark encodes bytes as "aa:bb:cc" or "aabbcc" depending on version,
        # and sometimes emits two comma-separated representations of the same
        # bytes field — take only the first.
        hex_bytes = hex_bytes.split(",")[0]
        hex_clean = hex_bytes.replace(":", "").replace(" ", "")
        try:
            blob = bytes.fromhex(hex_clean)
        except ValueError:
            continue
        if len(blob) != 296:
            continue
        try:
            cmd_int = int(cmd, 16) if cmd.startswith("0x") else int(cmd)
        except Exception:
            cmd_int = None
        # Channel: cmd low byte for 0x7700+ch reads and 0x1000N writes;
        # cmd itself for 0x04..0x07 writes.
        channel = (cmd_int & 0xFF) if cmd_int is not None else None
        if cmd_int is not None and 0x10000 <= cmd_int <= 0x1000F:
            channel = cmd_int & 0x0F
        rec = {
            "frame": int(frame_no),
            "timestamp": float(ts) if ts else None,
            "cmd": f"0x{cmd_int:X}" if cmd_int is not None else cmd,
            "cmd_name": cmd_name,
            "direction": direction,  # "0x51" etc. — tshark emits as string
            "continuation_of_frame": int(cont_of) if cont_of else None,
            "channel": channel,
            "blob_hex": blob.hex(),
            **decode_blob(blob),
        }
        records.append(rec)
    return records


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pcap", type=Path, help="capture file (.pcapng)")
    ap.add_argument("-o", "--output", type=Path,
                    help="output file (default: stdout, one JSON object per line)")
    ap.add_argument("--pretty", action="store_true",
                    help="pretty-print JSON with indentation (breaks jsonl format)")
    args = ap.parse_args()

    if not args.pcap.exists():
        print(f"ERROR: {args.pcap} not found", file=sys.stderr)
        return 2

    records = run_tshark(args.pcap)

    out = open(args.output, "w") if args.output else sys.stdout
    try:
        if args.pretty:
            json.dump(records, out, indent=2)
            out.write("\n")
        else:
            for rec in records:
                out.write(json.dumps(rec) + "\n")
    finally:
        if args.output:
            out.close()

    print(f"Exported {len(records)} reassembled blob(s)"
          + (f" to {args.output}" if args.output else ""),
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

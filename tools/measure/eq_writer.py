"""Set per-channel EQ bands on the DSP-408 via the dsp408-mqtt bridge's
raw/write topic. Avoids needing direct USB access from this Mac.

Encoding mirrors dsp408-py's set_eq_band (verified live):
    cmd  = 0x10000 + (band << 8) + channel
    cat  = 0x04 (CAT_PARAM)
    data = freq_lo, freq_hi, raw_lo, raw_hi, b4, 0, 0, 0
    raw  = round(gain_db * 10 + 600)
    b4   = round(256 / q), clamped 1..255 (b4=0x34 = Q≈4.7 = firmware default)

Usage examples:
    # Zero a single band on ch0:
    python eq_writer.py --broker 10.0.0.10 --device 4e9d357f5700 \\
        --channel 0 --band 1 --freq 149 --gain 0 --q 3.0

    # Bulk apply EQ from a JSON file:
    python eq_writer.py --broker 10.0.0.10 --device 4e9d357f5700 \\
        --bulk my_eq.json

    # Bulk zero (rewrite all listed bands with gain=0, useful as "EQ defeat"):
    python eq_writer.py --broker 10.0.0.10 --device 4e9d357f5700 \\
        --bulk-zero my_eq.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import paho.mqtt.client as mqtt


CMD_WRITE_EQ_BAND_BASE = 0x10000
CAT_PARAM = 0x04
CHANNEL_VOL_OFFSET = 600
EQ_Q_BW_CONSTANT = 256.0


def encode_band(channel: int, band: int, freq_hz: int,
                gain_db: float, q: float) -> tuple[int, int, str]:
    if not 0 <= channel <= 7:
        raise ValueError(f"channel 0..7, got {channel}")
    if not 0 <= band <= 9:
        raise ValueError(f"band 0..9, got {band}")
    raw = max(0, min(1200, round(gain_db * 10 + CHANNEL_VOL_OFFSET)))
    b4 = max(1, min(255, round(EQ_Q_BW_CONSTANT / q))) if q > 0 else 0x34
    payload = bytes([
        freq_hz & 0xFF, (freq_hz >> 8) & 0xFF,
        raw & 0xFF, (raw >> 8) & 0xFF,
        b4, 0, 0, 0,
    ])
    cmd = CMD_WRITE_EQ_BAND_BASE + (band << 8) + channel
    return cmd, CAT_PARAM, payload.hex()


def send_one(client, base, channel, band, freq, gain, q, expected_acks):
    cmd, cat, data_hex = encode_band(channel, band, freq, gain, q)
    msg = json.dumps({"cmd": cmd, "cat": cat, "data_hex": data_hex})
    expected_acks.append((channel, band, gain))
    client.publish(f"{base}/raw/write", msg, qos=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", required=True, help="MQTT broker host")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--device", required=True,
                    help="DSP-408 device slug (e.g. 4e9d357f5700)")
    ap.add_argument("--base-topic", default="dsp408")
    ap.add_argument("--channel", type=int)
    ap.add_argument("--band", type=int)
    ap.add_argument("--freq", type=int)
    ap.add_argument("--gain", type=float, default=0.0)
    ap.add_argument("--q", type=float, default=4.0)
    ap.add_argument("--bulk", help="JSON file: list of {ch,band,freq,gain,q}")
    ap.add_argument("--bulk-zero", help="JSON file with bands; rewrites each with gain=0")
    args = ap.parse_args()

    base = f"{args.base_topic}/{args.device}"
    acks = []

    def on_msg(c, u, m):
        try:
            d = json.loads(m.payload.decode())
            acks.append(d)
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_message = on_msg
    client.connect(args.broker, args.port, 10)
    client.subscribe(f"{base}/raw/write/ack")
    client.loop_start()
    time.sleep(0.3)

    expected = []
    if args.bulk or args.bulk_zero:
        path = args.bulk or args.bulk_zero
        with open(path) as f:
            entries = json.load(f)
        for e in entries:
            gain = 0.0 if args.bulk_zero else e["gain"]
            send_one(client, base, e["ch"], e["band"], e["freq"], gain,
                     e.get("q", 4.0), expected)
            time.sleep(0.05)
    else:
        if None in (args.channel, args.band, args.freq):
            sys.exit("Need --channel, --band, --freq (or --bulk).")
        send_one(client, base, args.channel, args.band, args.freq, args.gain,
                 args.q, expected)

    deadline = time.time() + 4.0
    while time.time() < deadline and len(acks) < len(expected):
        time.sleep(0.1)

    print(f"Sent {len(expected)} writes, got {len(acks)} acks.")
    err_count = sum(1 for a in acks if not a.get("chk_ok", True))
    if err_count:
        print(f"  WARNING: {err_count} acks had checksum issues.")
    client.loop_stop()
    client.disconnect()
    sys.exit(0 if len(acks) == len(expected) and err_count == 0 else 1)


if __name__ == "__main__":
    main()

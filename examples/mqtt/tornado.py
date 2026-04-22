"""Tornado: rotate music around 4 speakers, make-before-break style.

Each speaker dwells for `--dwell` seconds. Just before the next speaker
takes over, it's unmuted (overlap, ~50 ms) so there's no silent gap —
then the previous one is muted.

This is the binary-mute version. For a smooth equal-power crossfade,
see ``tornado_blend.py``.

Talks to the dsp408-mqtt bridge over MQTT — needs the bridge running
on the same network with HA-style per-channel mute topics:

    dsp408/<id>/ch<n>_mute/set    payload: ON | OFF

Channels assumed (1-indexed, matching the bridge's HA convention):
    ch1 = Front Right     ch2 = Front Left
    ch7 = Rear Right      ch8 = Rear Left

(Adjust ``--cycle`` if your speaker assignments differ.)

Example:
    python tornado.py --broker 10.21.0.138 --device 4e9d357f5700 \\
        --dwell 1.5 --duration 60
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

import paho.mqtt.client as mqtt


# Default cycle order (clockwise viewed from above, listener facing front):
#   FL → FR → RearR → RearL → ...
DEFAULT_CYCLE = [2, 1, 7, 8]
DEFAULT_LABELS = {1: "FR", 2: "FL", 7: "RearR", 8: "RearL"}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", required=True,
                    help="MQTT broker host (e.g. 10.21.0.138)")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--device", required=True,
                    help="Device slug (e.g. 4e9d357f5700)")
    ap.add_argument("--base-topic", default="dsp408",
                    help="Bridge's base topic (default 'dsp408')")
    ap.add_argument("--dwell", type=float, default=1.5,
                    help="seconds each speaker is dominant (default 1.5)")
    ap.add_argument("--overlap", type=float, default=0.25,
                    help="seconds of overlap during handoff (default 0.25)")
    ap.add_argument("--cycle", default=",".join(str(c) for c in DEFAULT_CYCLE),
                    help="comma-separated MQTT 1-indexed channels in rotation order "
                         "(default 2,1,7,8 = FL,FR,RearR,RearL)")
    ap.add_argument("--duration", type=float, default=None,
                    help="auto-stop after N seconds (default: forever, Ctrl-C to stop)")
    args = ap.parse_args()

    cycle = [int(x) for x in args.cycle.split(",")]
    if args.overlap >= args.dwell:
        sys.exit("overlap must be < dwell")

    base = f"{args.base_topic}/{args.device}"
    all_chs = list(range(1, 9))

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.port, 10)
    client.loop_start()

    def pub(ch: int, muted: bool):
        client.publish(f"{base}/ch{ch}_mute/set", "ON" if muted else "OFF")

    def cleanup(*_):
        print("\n  Cleaning up — unmute all in cycle", flush=True)
        for n in cycle:
            pub(n, False)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"🌪  Tornado — dwell={args.dwell}s, overlap={args.overlap}s")
    print(f"   Order: " + " → ".join(DEFAULT_LABELS.get(c, f"ch{c}") for c in cycle))
    print("   Ctrl-C to stop.\n", flush=True)

    # Initial: only cycle[0] unmuted; mute everything else in cycle
    for n in cycle:
        pub(n, n != cycle[0])
    time.sleep(0.5)

    t_start = time.time()
    i = 0
    while True:
        if args.duration and (time.time() - t_start) >= args.duration:
            cleanup()
        current = cycle[i]
        nxt = cycle[(i + 1) % len(cycle)]
        print(f"  {DEFAULT_LABELS.get(current, current):>5} (solo)", flush=True)
        time.sleep(args.dwell - args.overlap)
        # MAKE: unmute next BEFORE breaking current
        pub(nxt, False)
        time.sleep(args.overlap)
        # BREAK: mute current
        pub(current, True)
        i = (i + 1) % len(cycle)


if __name__ == "__main__":
    main()

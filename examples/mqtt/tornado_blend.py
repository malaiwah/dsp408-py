"""Tornado v2: equal-power crossfade rotation via routing-cell levels.

Same rotation as ``tornado.py`` but instead of binary mute, we ramp each
speaker's input-mixer cell levels (0..255 — 100 = unity per the
20·log10(level/100) dB curve) for a smooth perceptual handoff.

Equal-power curves preserve perceived loudness through the transition:

    old(t) = cos(t·π/2)   new(t) = sin(t·π/2)
    old² + new² = 1   (constant power across the crossfade)

Each speaker has 2 active input cells (its routing fan-in). All cells
of the speaker fade together. With a 1 s crossfade and 20 steps, that's
about 80 MQTT publishes per second — the bridge handles it without
breaking a sweat (verified live).

Talks to the dsp408-mqtt bridge over MQTT — needs the bridge running
with HA-style per-cell routing-level topics:

    dsp408/<id>/route/out<N>_in<M>/level/set    payload: 0..255

Channels assumed (1-indexed):
    out1=FR (in1+in3)   out2=FL (in2+in4)
    out7=RearR (in1+in3)   out8=RearL (in2+in4)

(Adjust ``--speakers`` JSON if your routing differs.)

Example:
    python tornado_blend.py --broker 10.21.0.138 --device 4e9d357f5700 \\
        --dwell 2.0 --crossfade 1.0 --duration 60
"""
from __future__ import annotations

import argparse
import json
import math
import signal
import sys
import time

import paho.mqtt.client as mqtt


# Default speaker map: out_ch -> (label, list of input cells feeding it)
DEFAULT_SPEAKERS = {
    1: ("FR",    [1, 3]),
    2: ("FL",    [2, 4]),
    7: ("RearR", [1, 3]),
    8: ("RearL", [2, 4]),
}
# Default cycle order (clockwise viewed from above)
DEFAULT_CYCLE = [2, 1, 7, 8]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", required=True)
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--device", required=True,
                    help="Device slug (e.g. 4e9d357f5700)")
    ap.add_argument("--base-topic", default="dsp408")
    ap.add_argument("--dwell", type=float, default=2.0,
                    help="seconds each speaker holds full level (default 2.0)")
    ap.add_argument("--crossfade", type=float, default=1.0,
                    help="seconds for equal-power crossfade (default 1.0)")
    ap.add_argument("--steps", type=int, default=20,
                    help="crossfade steps (default 20)")
    ap.add_argument("--cycle", default=",".join(str(c) for c in DEFAULT_CYCLE),
                    help="comma-separated MQTT output channels in rotation order")
    ap.add_argument("--speakers", default=None,
                    help="override speaker map as JSON: "
                         '{"1":[1,3], "2":[2,4], "7":[1,3], "8":[2,4]}')
    ap.add_argument("--duration", type=float, default=None,
                    help="auto-stop after N seconds")
    args = ap.parse_args()

    cycle = [int(x) for x in args.cycle.split(",")]
    if args.speakers:
        raw = json.loads(args.speakers)
        speakers = {int(k): (f"out{k}", list(v)) for k, v in raw.items()}
    else:
        speakers = DEFAULT_SPEAKERS

    base = f"{args.base_topic}/{args.device}"

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(args.broker, args.port, 10)
    client.loop_start()

    def set_level(out_ch: int, level: int):
        _, ins = speakers[out_ch]
        for in_ch in ins:
            client.publish(f"{base}/route/out{out_ch}_in{in_ch}/level/set",
                            str(int(level)))

    def cleanup(*_):
        print("\n  Cleaning up — restore all routing to 100", flush=True)
        for out_ch in speakers:
            set_level(out_ch, 100)
        time.sleep(0.5)
        client.loop_stop()
        client.disconnect()
        sys.exit(0)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    print(f"🌪  Tornado-blend — dwell={args.dwell}s, "
          f"crossfade={args.crossfade}s ({args.steps} steps)")
    print(f"   Order: " + " → ".join(speakers[c][0] for c in cycle))
    print("   Ctrl-C to stop.\n", flush=True)

    for out_ch in speakers:
        set_level(out_ch, 100 if out_ch == cycle[0] else 0)
    time.sleep(0.5)

    t_start = time.time()
    i = 0
    step_dt = args.crossfade / max(1, args.steps)

    while True:
        if args.duration and (time.time() - t_start) >= args.duration:
            cleanup()
        current = cycle[i]
        nxt = cycle[(i + 1) % len(cycle)]
        print(f"  {speakers[current][0]:>5} → {speakers[nxt][0]:<5}", flush=True)
        if args.dwell > args.crossfade:
            time.sleep(args.dwell - args.crossfade)
        for s in range(1, args.steps + 1):
            t = s / args.steps
            old_lev = round(100 * math.cos(t * math.pi / 2))
            new_lev = round(100 * math.sin(t * math.pi / 2))
            set_level(current, old_lev)
            set_level(nxt, new_lev)
            time.sleep(step_dt)
        set_level(current, 0)
        set_level(nxt, 100)
        i = (i + 1) % len(cycle)


if __name__ == "__main__":
    main()

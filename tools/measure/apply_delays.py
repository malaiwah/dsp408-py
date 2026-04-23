"""Apply per-channel delays via the dsp408-mqtt bridge, then read back.

Publishes to chN_delay/set, subscribes to chN_delay/state (retained), and
verifies the echo matches the requested value within ±1 sample.

Usage:
    python apply_delays.py --broker 10.21.0.138 --device 4e9d357f5700 \\
        --ch1 359 --ch2 359 --ch7 0 --ch8 318
"""
from __future__ import annotations

import argparse
import sys
import time

import paho.mqtt.client as mqtt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True)
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--device", required=True)
    ap.add_argument("--base-topic", default="dsp408")
    for n in range(1, 9):
        ap.add_argument(f"--ch{n}", type=int, default=None,
                        help=f"Delay (samples) for ch{n}")
    args = ap.parse_args()

    base = f"{args.base_topic}/{args.device}"
    targets = {n: getattr(args, f"ch{n}") for n in range(1, 9)
               if getattr(args, f"ch{n}") is not None}
    if not targets:
        sys.exit("Provide at least one --chN value.")

    seen: dict[int, int] = {}

    def on_connect(c, *_):
        for n in targets:
            c.subscribe(f"{base}/ch{n}_delay/state")

    def on_msg(c, u, m):
        n = int(m.topic.rsplit("/ch", 1)[1].split("_")[0])
        try:
            seen[n] = int(m.payload.decode())
        except Exception:
            pass

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_msg
    client.connect(args.broker, args.port, 10)
    client.loop_start()
    time.sleep(0.5)

    # Capture pre-state
    pre = dict(seen)
    print("Pre-state (retained chN_delay/state values):")
    for n in sorted(targets):
        print(f"  ch{n}: {pre.get(n, '<no retained value>')}")

    print("\nApplying:")
    for n, v in sorted(targets.items()):
        client.publish(f"{base}/ch{n}_delay/set", str(v), qos=1)
        print(f"  ch{n} → {v}")

    # Wait for state echoes to settle
    deadline = time.time() + 3.0
    while time.time() < deadline:
        if all(seen.get(n) == v for n, v in targets.items()):
            break
        time.sleep(0.1)

    print("\nReadback (post chN_delay/state):")
    ok = True
    for n, v in sorted(targets.items()):
        got = seen.get(n)
        match = "OK" if got == v else "MISMATCH"
        if got != v:
            ok = False
        print(f"  ch{n}: requested={v:>5}  got={str(got):>5}  {match}")

    client.loop_stop()
    client.disconnect()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

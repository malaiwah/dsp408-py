# MQTT examples

Small, fun examples that drive a DSP-408 entirely through the
`dsp408-mqtt` bridge — no direct USB required from the running machine.

These also doubled as **sustained-write stress tests** during the
2026-04-22 library hardening pass: ~1500 routing-level publishes over
2 minutes left every active channel's EQ, HPF, routing, and `spk_type`
byte-exactly intact (verified via raw/read snapshots before/after).

## tornado.py

Rotates a single playing speaker around the room, **make-before-break**
style — the next speaker is unmuted ~250 ms before the previous one is
muted, so there's no silence at the handoff. Binary mute, no level
ramp.

```sh
pip install paho-mqtt
python tornado.py --broker 10.21.0.138 --device 4e9d357f5700 \
    --dwell 1.5 --duration 60
```

## tornado_blend.py

Same rotation, but smooth **equal-power crossfade** via the input mixer
levels (`route/out<N>_in<M>/level/set`). Each speaker's input cells
fade together using `cos(t·π/2)` / `sin(t·π/2)` curves — perceived
loudness stays constant through the transition.

```sh
python tornado_blend.py --broker 10.21.0.138 --device 4e9d357f5700 \
    --dwell 2.0 --crossfade 1.0 --duration 60
```

## Default speaker assignment

Both scripts default to a 4-corner bipolar setup with this mapping:

| MQTT 1-indexed channel | Physical speaker | Routing inputs |
|---|---|---|
| ch1 / out1 | Front Right | IN1 + IN3 |
| ch2 / out2 | Front Left  | IN2 + IN4 |
| ch7 / out7 | Rear Right  | IN1 + IN3 |
| ch8 / out8 | Rear Left   | IN2 + IN4 |

Override with `--cycle` (rotation order) and, for the blend version,
`--speakers` (per-output input cell list as JSON).

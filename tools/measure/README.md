# tools/measure — REW-like room-acoustics toolchain

Mac/Linux tools for measuring a room with a USB mic (UMIK-1 style),
producing REW-compatible `.txt` exports that downstream scripts can
analyze. Works **split-machine**: the audio I/O runs on a Mac, the
DSP-408 control runs via the `dsp408-mqtt` bridge on a separate host
(e.g. a Raspberry Pi), coordinated over MQTT. No USB-to-DSP connection
needed on the machine that owns the mic and audio output.

## The tools

### Frequency-response measurement

| Script | What |
|---|---|
| `measure.py` | Play a Farina log sweep → capture UMIK-1 → deconvolve to IR → FFT to FR → apply mic cal → write REW-format `.txt`. Single-channel, no DSP coordination. |
| `iterate_all.py` | Drive the MQTT bridge to mute all-but-one speaker in turn and run `measure.py` for each. Produces 4 solo + 1 all-4 measurements hands-free. |
| `balance.py` | Compute per-speaker SPL (band-limited RMS, default 500-2000 Hz) from existing sweep `.txt` files; output DSP-408 per-channel volume trims to flatten the mix at the mic position. |
| `state_snapshot.py` | Snapshot semantic state of all 8 channels via MQTT `raw/read`; diff two snapshots. Used for regression-testing writes — confirms nothing was mutated outside the fields touched. |

### Time alignment (impulse response + per-channel delay)

| Script | What |
|---|---|
| `measure_ir.py` | Capture one room IR using a 2-input USB interface (Scarlett 2i2 4th gen tested). Mic on input 1, hardware loopback on input 3 → deconvolve `mic ÷ ref` so the IR is in the reference frame of the actual electrical signal. DAC / cable / DSP constants cancel across speakers, so peak times become directly comparable. CLI is compatible with `iterate_all.py --measure-script` so the existing solo-cycle orchestrator can drive it unchanged. |
| `compute_delays.py` | Load 4 `.npz` IRs (FR/FL/RearR/RearL produced by `measure_ir.py`), find sub-sample peak times by parabolic interpolation, anchor on the farthest speaker, and emit a ready-to-paste dsp408-py snippet of per-channel `delay_samples` to add. Reports air-path differences (m/s) as a sanity check. |
| `apply_delays.py` | Push per-channel delay (samples @ 48 kHz) to the DSP-408 via the bridge's `chN_delay/set` topic, with retained-state readback verification. NOTE: the firmware silently caps at 359 samples (~7.5 ms) per channel even though the SDK accepts u16. |

### EQ control over MQTT (no USB needed locally)

| Script | What |
|---|---|
| `eq_writer.py` | Set per-channel parametric-EQ bands by encoding `set_eq_band` calls into `raw/write` JSON to the bridge. Supports single-band `--channel/--band/--freq/--gain/--q` form and `--bulk path.json` for full presets, plus `--bulk-zero path.json` to defeat all bands listed (useful as an "EQ-off" baseline for A/B testing). Useful when the mic machine and DSP host are different boxes. |

## Dependencies

```sh
pip install sounddevice numpy scipy paho-mqtt
```

`sounddevice` needs PortAudio (Mac: built-in; Linux: `apt install libportaudio2`).

## Typical workflow

```sh
# 1. Start the dsp408-mqtt bridge on the host with the DSP-408 USB
#    connection. (systemd unit ships in packaging/.)

# 2. From the mic machine, measure all 4 speakers at a listening position:
python iterate_all.py \
    --broker 10.21.0.138 \
    --device 4e9d357f5700 \
    --prefix "P1 EQon 256k" \
    --sweep-length 262144 \
    --cal-file ~/Downloads/7080334_90deg.txt \
    --also-all-four

# 3. Analyze level balance at that position:
python balance.py --prefix P1_EQon_256k
```

Output is filenames like `P1_EQon_256k_{fr,fl,rear_r,rear_l,all4}.txt`,
directly readable by REW for plotting / detailed analysis.

## Time-alignment workflow (Scarlett 2i2 + any mic)

```sh
# 1. Mic on Scarlett input 1 (any uncalibrated mic — magnitude doesn't
#    matter for timing, the ref-loopback removes it). 2i2 4th gen exposes
#    hardware loopback as inputs 3+4; no cable jumper needed.

# 2. Solo-cycle through the 4 speakers, capturing IRs:
python iterate_all.py \
    --broker 10.21.0.138 --device 4e9d357f5700 \
    --prefix "P1_IR" --sweep-length 131072 \
    --measure-script ./measure_ir.py \
    --output-device 1 --input-device 1   # both = Scarlett

# 3. Compute and apply per-channel delays:
python compute_delays.py --prefix P1_IR
# … inspect the proposed delays, then either copy the dsp408-py snippet
#   into a script, or push them via MQTT directly:
python apply_delays.py \
    --broker 10.21.0.138 --device 4e9d357f5700 \
    --ch1 102 --ch2 186 --ch7 0 --ch8 0
```

The hardware loopback path makes timing immune to OS audio-stack jitter:
`mic` and `loopback ref` share the same ADC clock, and constants
(DAC + cable + DSP + speaker emit + ADC) cancel across speakers, leaving
pure relative time-of-flight. Sub-sample peak interpolation gets you
~mm precision (well below the audible threshold).

## Assumptions & limitations

- **Mic calibration**: bring your own `.txt` file in UMIK-1 format
  (per-serial — download from MiniDSP's site). Without `--cal-file`,
  absolute SPL is approximate but relative shape is tight.
- **Per-mic headers**: `measure.py` reads `Sens Factor` and `AGain`
  from the cal-file header (UMIK-1 format) to compute absolute SPL.
  A pistonphone check is still the best calibration — we're within
  ±3 dB of REW typically.
- **IR window**: 300 ms quasi-anechoic (REW's default is similar).
  Widening helps LF resolution but smears modal ringing into the
  response — only do it if you want to see the room's natural
  response including ring.
- **Two USB devices, two clocks**: when Scarlett + UMIK-1 are both
  active on a Mac, drift between them is sub-sample for sweeps <15 s.
  For longer/tighter work, an aggregate device in Audio MIDI Setup
  with drift correction is the path.
- **Channel assignments** in `iterate_all.py` default to the author's
  4-corner bipolar setup; override with `--speakers`. Channels are
  MQTT-1-indexed to match the bridge's HA-discovery convention.
- **Delay firmware cap**: `apply_delays.py` will happily round-trip any
  u16 value, but the firmware silently caps each channel at 359 samples
  (≈7.5 ms @ 48 kHz). For larger spreads you'll need to anchor on a
  closer speaker and accept the farthest one as "the late one."

## Verified reproducibility

Two back-to-back runs at the same mic position on 2026-04-22:

| Channel | Shape σ (80-500 Hz) | RMS Δ across 11 target freqs |
|---|---|---|
| FR | 4.75 → 4.57 | 0.54 dB |
| FL | 3.99 → 3.94 | 0.59 dB |
| RearL | 5.20 → 5.20 | 0.67 dB |
| All-4 | 6.09 → 5.98 | 0.64 dB |

~0.5 dB repeatability on smooth regions, 1-2 dB at modal extremes
(deep-null zones are inherently flaky). Consistent with REW.

## Related

- `examples/mqtt/` — fun MQTT-driven utilities (tornado rotation
  demos) that double as write-rate stress tests.
- `tools/wireshark/` — USB packet dissector for the DSP-408 wire
  protocol.

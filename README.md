# dsp408-py — Dayton Audio DSP-408 USB control, reverse-engineered

[![CI](https://github.com/malaiwah/dsp408-py/actions/workflows/ci.yml/badge.svg)](https://github.com/malaiwah/dsp408-py/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

A from-scratch, cross-platform (Linux/macOS) implementation of the
Dayton Audio **DSP-408** USB control protocol, reverse-engineered from
USB packet captures of my own DSP-408 hardware.

> **Disclaimer.** This is an independent interoperability project. It is
> **not** affiliated with, endorsed by, or associated with Dayton Audio
> in any way. "Dayton Audio" and "DSP-408" are trademarks of their
> respective owners; mentioned here solely to identify the hardware
> this driver targets.
>
> The reverse-engineering was done for the limited purpose of writing
> an interoperable driver for hardware I own — protected speech under
> US DMCA §1201(f) (interoperability exemption) and EU Software
> Directive 2009/24/EC Article 6. No copyrighted vendor code is
> redistributed in this repository: the firmware binaries, Windows
> installer, and Android APK that informed the work are referenced by
> source URL only. The driver itself is original Python written
> clean-room; its public API is fact (wire-protocol observations) and
> its implementation is mine. Provided as-is under MIT, with no
> warranty and no claim of fitness for any particular purpose.
>
> **Use at your own risk.** Talking USB-HID to your DSP-408 with
> non-vendor software may void warranty support from Dayton. Firmware
> flashing in particular has bricking potential — the included
> `dsp408 flash` recovery path was tested on the author's hardware
> only. If you're not willing to handle a stuck-in-DFU device, use
> the official Windows app for firmware updates.

Two things this stack does that the Windows app does *not*:

* **Controls multiple DSP-408s at once** — the official app is single-device.
* **Exposes everything over MQTT with Home Assistant auto-discovery** —
  drop the bridge on a Pi and every DSP-408 plugged into it shows up as
  a device in HA automatically, with native HA entities for every
  audibly-functional control surface in the firmware.

## What works (firmware v1.06, ``MYDW-AV1.06``)

Each entry below has been **live-validated end-to-end** against real
audio through a Scarlett 2i2 loopback rig — not just wire round-trip,
but actual measured dB / phase / time-domain response.

| Subsystem          | Status                                              |
|--------------------|-----------------------------------------------------|
| Transport (HID)    | **Working** — `80 80 80 ee` envelope, single + multi-frame reads + writes |
| Connect / identity | **Working** — `dsp408 info` returns `MYDW-AV1.06`   |
| Multi-device       | **Working** — select by index / serial / path; HA bridge spawns one worker per device |
| Master volume + mute | **Working** — `cmd=0x0005 cat=0x09`; calibrated -60..+6 dB |
| Per-output volume + mute | **Working** — `cmd=0x1f0X cat=0x04`; calibrated -60..0 dB |
| Per-output delay  | **Working** — sample-accurate, capped at 359 taps (8.14 ms @ 44.1 kHz) |
| Per-output phase invert | **Working** — verified ±180° via Scarlett correlation |
| Per-output channel name | **Working** — `cmd=0x24XX`; 8-byte ASCII at blob[286..293] |
| Routing matrix 8×4 | **Working** — `cmd=0x21XX`; u8 0..255 cells, calibrated `20·log10(level/100)` dB curve, +8 dB headroom at 255 |
| Crossover (HPF + LPF) | **Working** — `cmd=0x1200X`; BW/Bessel/LR + LR-alias, slopes 6..48 dB/oct + bypass at slope=8 |
| 10-band parametric EQ | **Working** — `cmd=0x10X0Y`; freq + gain + Q (Q ≈ 256/b4 fixed-point reciprocal) |
| 296-byte full-channel-state write | **Working** — atomic restore via multi-frame WRITE (matches GUI bytes verbatim; one documented 2-byte firmware quirk that the GUI also exhibits) |
| Save preset to flash | **Working** — `cmd=0x34=01` trigger + bulk per-channel writes |
| Per-input phase invert | **Working** — only the input MISC field with audio effect (the rest are firmware placeholders, see below) |
| Firmware flash     | **Working** — proven on Windows + recovery path; **bricking risk — see disclaimer** |
| MQTT + HA discovery| **Working** — one device-based config per DSP-408; per-poll state refresh |
| Gradio web UI      | Master / channel name / per-input polar / **live mixer matrix** wired; per-channel EQ + crossover sliders are layout placeholders (use the typed library API or `raw` MQTT topic for those) |

## What's NOT in firmware v1.06

Hardware features that **exist on the wire / in the data model but
the firmware doesn't actually implement**. Each was confirmed inert
by 3-to-4 independent lines of evidence (loopback rig measurements,
firmware disassembly, Android app source, Windows GUI inspection):

| Feature                | Status              | Evidence  |
|------------------------|---------------------|-----------|
| **Compressor / limiter**   | **DEAD** in v1.06   | Audio rig + firmware disasm + leon Android source + Windows GUI all confirm the block isn't wired to audio. The wire encoding decodes correctly (`cmd=0x230X`, lands at blob[278..285]) and round-trips through reads, but no parameter — threshold, attack, release, all_pass_q, linkgroup — produces audible compression at any setting. The Windows GUI doesn't expose compressor controls anywhere. |
| **VU / live audio meters** | **DEAD** in v1.06   | Both READ candidates (`cmd=0x13` and `cmd=0x03` idle-poll) return completely static bytes across full input-level / mute / route sweeps. Firmware disasm finds no audio-level computation path. leon Android source has no decoder for any meter frame. Windows GUI has no level visualization. |
| **Per-input mute / volume / delay / EQ / noisegate** | **DEAD** in v1.06 | Wire writes round-trip exactly through `read_input_state` (288-byte blob at `cat=0x03`), but only **input phase invert** affects audio. Mute, volume (full u8 sweep), delay, 15-band EQ, and noisegate all measured 0 dB / 0 sample / 0 dB-peak change. |
| **Speaker-template tonal shaping** | Misnamed | `apply_speaker_template()` writes the spk_type byte (blob[253]). It does NOT apply a speaker-specific HPF/LPF/EQ — different templates produce identical frequency response. What it actually does is reassign the channel to a different DSP slot which may have ~+18 dB of internal pre-gain. Treat as "DSP slot picker", not "speaker preset". |

The corresponding Python APIs (`set_compressor`, `set_input`,
`apply_speaker_template`, etc.) are **kept** so that:
1. Wire encoding stays documented and exposed for forward-compat with
   any future firmware revision that activates these blocks.
2. Bytes round-trip exactly, so they're safe for state-preservation
   workflows (read → modify → write).
3. Each carries a clear "INERT" / "DEAD" docstring so callers aren't
   misled.

## Install

```bash
# from a clone of this repo, on Linux or macOS:
uv sync --extra ui --extra mqtt     # library + Gradio UI + MQTT bridge
# or picking and choosing:
uv sync --extra ui                  # just the web UI
uv sync --extra mqtt                # just the MQTT bridge
uv sync                             # library only
```

This installs `hidapi` (required) plus `gradio` and `paho-mqtt` as
optional extras. On Linux you also need the `libhidapi-libusb0` system
package and a udev rule to let your user open `/dev/hidraw*`:

```
# /etc/udev/rules.d/60-dsp408.rules
# Running firmware — cython-hidapi from PyPI uses libusb on Linux so we need
# the `usb` subsystem rule in addition to hidraw:
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5750", MODE="0660", GROUP="plugdev", TAG+="uaccess"
SUBSYSTEM=="usb",    ATTRS{idVendor}=="0483", ATTRS{idProduct}=="5750", MODE="0660", GROUP="plugdev", TAG+="uaccess"
# STM32 DFU bootloader (for `dsp408 flash` recovery path):
SUBSYSTEM=="usb",    ATTRS{idVendor}=="0483", ATTRS{idProduct}=="df11", MODE="0660", GROUP="plugdev", TAG+="uaccess"
```

Reload udev after editing:

```bash
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## Device aliases

When you have more than one DSP-408 plugged into the same host, the
default `display_id` (serial if present, otherwise a `dsp408-<hash>`
derived from the USB path) is stable but unfriendly. Drop a small TOML
file to give each unit a human name:

```toml
# ~/.config/dsp408/aliases.toml
[aliases]
"4EAA4B964C00"    = "Living Room Subs"
"dsp408-cf594b63" = "Garage Amp"
```

Keys are matched against (in order) the device's `serial_number`,
`display_id`, or stringified hidapi path — so `dsp408 list` tells you
exactly what string to use. Friendly names then surface everywhere:

* `dsp408 list` shows them in a dedicated column.
* `dsp408 --device "Living Room Subs" info` — selectors match friendly
  names just like index / serial / path.
* The Gradio dropdown labels entries `[N] Living Room Subs · 4EAA4B964C00`.
* Home Assistant discovery uses the alias as the MQTT device name, so
  the card in HA says "Living Room Subs" instead of "DSP-408 (4EAA…)".

Search order (later wins, so user config overrides system-wide):

1. `/etc/dsp408/aliases.toml`
2. `$XDG_CONFIG_HOME/dsp408/aliases.toml` (default `~/.config/dsp408/aliases.toml`)
3. `./dsp408-aliases.toml` (working directory)

Or pass `--aliases PATH` to any `dsp408` subcommand to load a single
file explicitly and skip the search.

## CLI

```bash
uv run dsp408 list                      # enumerate every DSP-408
uv run dsp408 info                      # first device
uv run dsp408 --device 1 info           # second device (by index)
uv run dsp408 --device MYDW-AV1234 info # select by serial
uv run dsp408 --device "Garage Amp" info  # select by alias (see below)
uv run dsp408 --aliases ./my.toml list    # use an explicit aliases file
uv run dsp408 snapshot                  # full startup handshake dump
uv run dsp408 read 0x04                 # raw read by cmd code
uv run dsp408 read-channel 0            # 296-byte channel-state blob
uv run dsp408 write 1f07 "01 00 96 01 00 00 00 12" --cat 04
uv run dsp408 poll --interval 1
uv run dsp408 flash path/to/DSP-408-Firmware.bin
uv run dsp408 mqtt --broker mqtt.local --username ha --password secret
```

## Web UI

![Gradio web UI screenshot](docs/webui.png)

```bash
uv run python -m webui.app --host 0.0.0.0 --port 7860
# on the Pi, browse to http://<pi-ip>:7860
```

Tabs:
- **Device dropdown** (top of page) — switch between multiple DSP-408s live.
- **Channels** — placeholder widgets wired with correct ranges from the
  manual; write path not hooked up until `0x77NN` layout is decoded.
- **Mixer** — 4×8 routing matrix (placeholder).
- **Snapshot** — startup dump + raw 0x77NN channel reader.
- **Raw Console** — send any `80 80 80 ee`-framed READ/WRITE command,
  see the reply bytes. This is the experimentation surface for the
  live reverse-engineering work.
- **Firmware** — flash any `.bin` image (targets the device currently
  selected in the dropdown). Lifesaver: bypasses HID Usage Page matching
  so it recovers a device that's been flashed with a patched descriptor.

## MQTT / Home Assistant bridge

Run the bridge on whichever host has the DSP-408s plugged in (e.g. a
Raspberry Pi):

```bash
uv run dsp408 mqtt --broker homeassistant.local --username ha --password secret
# or with a custom topic prefix:
uv run dsp408 mqtt --broker 192.168.1.5 --topic-prefix audio/dsp408
```

Each attached DSP-408 auto-registers as a separate **device** in Home
Assistant (discovery topic `homeassistant/device/dsp408_<id>/config`,
HA 2024.12+ device-based format).

**Entities exposed today** (per DSP-408):

| Entity              | HA type    | Direction | Notes                                      |
|---------------------|------------|-----------|--------------------------------------------|
| Firmware identity   | sensor     | read-only | e.g. `MYDW-AV1.06`; `diagnostic`           |
| Preset name         | text       | r/w       | rename the active preset (≤15 chars)       |
| Status byte         | sensor     | read-only | numeric; `diagnostic`                      |
| State 0x13          | sensor     | read-only | 10-byte hex blob (semantics unknown); `diagnostic` |
| Global 0x06         | sensor     | read-only | hex blob; `diagnostic`                     |
| **Master volume**   | number     | **r/w**   | slider, -60..+6 dB (1 dB step)             |
| **Master mute**     | switch     | **r/w**   | ON = muted                                 |
| **Channel N volume** (×8) | number | **r/w**  | slider, -60..0 dB (0.5 dB step), reads from blob |
| **Channel N mute** (×8)   | switch | **r/w**  | ON = muted                                |
| **Channel N polar** (×8)  | switch | **r/w**  | phase invert (180°)                       |
| **Channel N delay** (×8)  | number | **r/w**  | samples (0..359 taps; ~8.14 ms @ 44.1 kHz) |
| **Channel N state** (×8)  | sensor (JSON) | r/w  | one diagnostic JSON per channel: crossover, mixer, compressor, link group, channel name |
| **Out N ← In M** routing (×32) | switch + number | **r/w** | input-routing matrix; bool ON/OFF + numeric level (0..255) per cell; `config` |
| **Factory reset / Load preset** | button | **write** | ⚠ **KNOWN-BROKEN** — magic-word stub, wire encoding still unverified |

Per-channel state is now read from the 296-byte channel blob both at
startup and on every poll, so HA stays in sync even after restarts or
out-of-band changes from the official GUI. EQ-band, crossover, and
compressor *write* APIs exist in :mod:`dsp408.device` but are not yet
mapped to HA entities (would explode the entity count); reach them
from HA via the `raw/write` topic.

**Routing matrix layout:** the DSP-408 boots with all 32 routing cells
OFF (no audio path). To get audio flowing, toggle the relevant
`Out N ← In M` switches in HA (e.g. for stereo: Out1←In1, Out2←In2).
The bridge persists each toggle to the device immediately and to MQTT
retained state so it survives a restart.

**Availability:** there's a per-device topic `dsp408/<id>/status` plus
a bridge-level LWT on `dsp408/bridge/status`, combined with
`avty_mode: all`. If the bridge process dies or the USB handle
stops answering, *every* device's entities flip to "unavailable" in
HA immediately.

Plus a raw-protocol channel for custom automations (this is the real
escape hatch until the high-level entities land):

```
Topic                                  Payload
dsp408/<id>/raw/read                   {"cmd":"0x04","cat":"0x09"}
dsp408/<id>/raw/read/reply             {"cmd":"0x04", "payload_hex":"...", ...}
dsp408/<id>/raw/write                  {"cmd":"0x1f07","cat":"0x04","data_hex":"010096010000001 2"}
dsp408/<id>/raw/write/ack              {"dir":"0x51", ...}
```

The bridge re-enumerates hot-plugged devices every ~1 second, so
plugging or unplugging a DSP-408 while the bridge is running spawns
or reaps the matching worker thread without a restart.

### Running the bridge as a systemd service

A ready-to-go unit lives at `packaging/systemd/dsp408-mqtt.service`,
paired with `dsp408-mqtt.env.example` for host-specific config
(broker address, credentials, aliases path). Install it once:

```bash
sudo cp packaging/systemd/dsp408-mqtt.service /etc/systemd/system/
sudo cp packaging/systemd/dsp408-mqtt.env.example /etc/default/dsp408-mqtt
sudoedit /etc/default/dsp408-mqtt        # set DSP408_BIN + DSP408_ARGS
sudo systemctl daemon-reload
sudo systemctl enable --now dsp408-mqtt
```

Inspect:

```bash
systemctl status dsp408-mqtt
journalctl -u dsp408-mqtt -f
```

`Restart=on-failure` auto-recovers from USB or broker blips,
`KillSignal=SIGTERM`+`TimeoutStopSec=10` gives the bridge a chance to
publish `offline` on every availability topic before exit — so
`systemctl restart dsp408-mqtt` flips the devices to unavailable in
HA cleanly rather than leaving stale state.

## Library

```python
from dsp408 import Device, enumerate_devices

for info in enumerate_devices():
    print(f"[{info['index']}] {info['display_id']}  {info['path']!r}")

with Device.open(selector=0) as dev:
    info = dev.snapshot()
    print(info.identity)              # "MYDW-AV1.06"
    print(info.preset_name)           # e.g. "test"
    ch1 = dev.read_channel_state(0)   # 296 raw bytes
    # Raw escape hatches for experiments:
    reply = dev.read_raw(cmd=0x04, category=0x09)
    dev.write_raw(cmd=0x1f07,
                  data=bytes.fromhex("010096010000001 2"),
                  category=0x04)
```

See `dsp408/__init__.py` for the full public API and `dsp408/protocol.py`
for the wire format.

## Protocol summary

```
                       64-byte HID report on EP 0x01 OUT / 0x82 IN
offset  len  field            notes
0       4    magic            80 80 80 ee
4       1    direction        a2 (read req) / a1 (write) / 53 (read rep) / 51 (ack)
5       1    version          01
6       1    seq              host-chosen, mirrored by device
7       1    category         09 = state, 04 = parameter
8..11   4    cmd              LE u32
12..13  2    payload length   LE u16
14..N   len  payload
14+len  1    checksum         XOR of bytes[4 .. 14+len-1]
15+len  1    end marker       aa
rest         padding          00 ...
```

Full analysis — multi-frame reads, firmware upload flow, bootloader
integrity finding, 7 decoded Windows USBPcap captures, macOS IOKit
seizure experiments, and a DriverKit `.dext` stub — lives on the
[`reverse-engineering`](../../tree/reverse-engineering) branch.

## Tests

```bash
uv run pytest -q          # verifies frame builder against on-the-wire bytes
```

Tests cover frame round-trips against literal capture bytes (15),
multi-device enumeration logic (11), MQTT discovery shape + paho
v1/v2 compatibility (9), and device-alias loading / lookup (13).
No real USB or broker required.

## Reverse-engineering write-ups

Decoded protocol findings, blob-layout maps, and per-feature audio-
validation results live on the
[`reverse-engineering`](../../tree/reverse-engineering) branch under
`notes/`. Includes per-output 296-byte channel state breakdown,
Android v1.23 source-mining results, captures from the official
Windows GUI, and the negative-result evidence that pinned down which
firmware blocks are truly inert.

```bash
git fetch origin reverse-engineering
git checkout reverse-engineering
```

The branch references — but does **not** include — the Dayton firmware
binaries, Windows installer, and Android APK that informed the work.
Source URLs for those are listed in `notes/SOURCES.md` so anyone
extending the work can fetch them directly from Dayton / the Play
Store and reproduce the analysis on their own copy.

## Hardware facts (live-verified, 2026-04-19)

4 RCA + 4 high-level inputs, 8 RCA outputs, 10-band parametric EQ per
output (all peaking — the manual's mention of LS/HS shelves on bands
1 & 10 is not present in the wire format and was not exposed in
firmware), HPF + LPF per output (Butterworth / Bessel / Linkwitz-Riley
+ a 4th filter-type byte that aliases LR; slopes 6/12/18/24 dB/oct
per the manual but firmware accepts the full 6..48 + bypass; 20 Hz –
20 kHz), per-output delay up to 8.1471 ms (277 cm) @ 44.1 kHz / 7.48
ms @ 48 kHz, 6 named preset slots in flash, master volume, 4×8
input→output mixer (cells take u8 0..255 levels — the GUI exposes
non-binary cells behind a click-through; firmware accepts the full
range with a clean `20·log10(level/100)` dB curve and ~+8 dB headroom
above unity).

The DSP-408 is **HID-only** — it does NOT enumerate as a USB Audio
Class device. Audio I/O happens entirely on the analog jacks; the
USB interface only carries control HID frames.

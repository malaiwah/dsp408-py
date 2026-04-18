# usb_dsp_mac — Dayton Audio DSP-408 USB control, reverse-engineered

A from-scratch, cross-platform (Linux/macOS) implementation of the
Dayton Audio **DSP-408** USB control protocol, reverse-engineered from
Windows USBPcap captures of the official `DSP-408.exe` V1.24 GUI.

| Subsystem          | Status                                              |
|--------------------|-----------------------------------------------------|
| Transport (HID)    | **Working** — `80 80 80 ee` envelope, single + multi-frame reads |
| Connect / identity | **Working** — `dsp408 info` returns `MYDW-AV1.06`   |
| Preset name read/write | **Working**                                     |
| Firmware flash     | **Working** — proven on Windows, incl. recovery path |
| Channel state read (`0x77NN`) | **Raw bytes only** — layout still TBD    |
| Parameter write (`0x1fNN`) | **Framing correct** — sub-index → param mapping TBD |
| Mixer 4×8 routing  | Not implemented                                     |
| Gradio web UI      | Skeleton + raw console + firmware flash; typed widgets are placeholders |

## Install

```bash
# from a clone of this repo, on Linux or macOS:
uv sync --extra ui          # installs dsp408 + gradio
# or without the UI:
uv sync
```

This installs `hidapi` (required) and `gradio` (optional). On Linux you
also need the `libhidapi-libusb0` system package and udev rules that
let your user open `/dev/hidraw*` — the repo's Linux CI + Pi
instructions cover this.

## CLI

```bash
uv run dsp408 list                 # enumerate
uv run dsp408 info                 # CONNECT + GET_INFO + preset name
uv run dsp408 snapshot             # full startup handshake dump
uv run dsp408 read 0x04            # raw read by cmd code
uv run dsp408 read-channel 0       # 296-byte channel-state blob
uv run dsp408 write 1f07 "01 00 96 01 00 00 00 12" --cat 04
uv run dsp408 poll --interval 1    # live state watcher
uv run dsp408 flash firmware_patch/DSP-408-Firmware-V6.21-PATCHED-hidpage.bin
```

## Web UI

```bash
uv run python -m webui.app --host 0.0.0.0 --port 7860
# on the Pi, browse to http://<pi-ip>:7860
```

Tabs:
- **Channels** — placeholder widgets wired with correct ranges from the
  manual; write path not hooked up until `0x77NN` layout is decoded.
- **Mixer** — 4×8 routing matrix (placeholder).
- **Snapshot** — startup dump + raw 0x77NN channel reader.
- **Raw Console** — send any `80 80 80 ee`-framed READ/WRITE command,
  see the reply bytes. This is the experimentation surface for the
  live reverse-engineering work.
- **Firmware** — flash any `.bin` image. Lifesaver: bypasses HID Usage
  Page matching so it recovers a device that's been flashed with a
  patched descriptor.

## Library

```python
from dsp408 import Device

with Device.open() as dev:
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

Full analysis (including multi-frame reads, firmware upload flow,
bootloader integrity finding, and 7 decoded Windows captures) lives in
`captures/`.

## Tests

```bash
uv run pytest -q          # verifies frame builder against on-the-wire bytes
```

## Related files

- `captures/README.md` — capture methodology + findings log
- `firmware_patch/README.md` — patched-firmware experiment (noop + HID Usage Page)
- `flash_firmware.py` — standalone Windows-tested flasher (predates the library)
- `dsp408_legacy.py` — the abandoned DLE/STX implementation (TCP protocol, wrong for USB)

## Hardware facts (from the manual, for reference)

4 RCA + 4 high-level inputs, 8 RCA outputs, 10-band PEQ per output,
HPF + LPF per output (Linkwitz-Riley / Bessel / Butterworth, slopes
6/12/18/24 dB/oct, 20 Hz – 20 kHz), per-channel delay up to 8.1471 ms
(277 cm), 6 presets, master volume, 4×8 input→output mixer.

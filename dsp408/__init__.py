"""dsp408 — Dayton Audio DSP-408 USB control library.

Implements the `80 80 80 ee` HID transport protocol reverse-engineered from
Windows USBPcap captures of the official DSP-408.exe V1.24 GUI.

Top-level API:

    from dsp408 import Device, DeviceNotFound

    with Device.open() as dev:
        dev.connect()
        print(dev.get_info())            # "MYDW-AV1.06"
        print(dev.read_preset_name())    # e.g. "test"
        raw = dev.read_channel_state(0)  # 296 bytes of channel 1 params

See `dsp408.protocol` for frame-level constants, and `dsp408.flasher` for
firmware upload. This library is cross-platform (hidapi), designed to
run on Linux (Raspberry Pi) and macOS; first live bring-up is planned
against the DSP-408 attached to a Raspberry Pi.

Device specs (from the manual + live verification on the loopback rig):
  * 4 RCA + 4 high-level inputs
  * 8 RCA outputs
  * 10-band parametric EQ per output channel (live-verified — all bands
    behave as peaking; the manual's mention of LS/HS shelves on bands 1
    & 10 has not been observed in the wire format and there's no shelf
    flag in the encoding).
  * Independent HPF + LPF per channel — types: Butterworth, Bessel,
    Linkwitz-Riley (filter-type byte 3 also produces an LR response,
    confirmed live).  Slopes: 6/12/18/24/30/36/42/48 dB/oct (firmware
    accepts the full set even though the GUI/manual show 6..24 only),
    plus an explicit "Off" / bypass setting (slope byte = 8).
    Freq 20 Hz – 20 kHz.
  * Per-channel delay — wire format is samples (u16). Firmware caps at
    359 taps: that's 8.14 ms @ 44.1 kHz (matching the manual's "8.1471 ms /
    277 cm" claim) but only 7.48 ms when the device runs at 48 kHz.
  * 4×8 input→output mixer matrix — cells take 0..255 u8 levels (linear
    amplitude, allows boost above unity), not just on/off as the GUI
    exposes.
  * 6 named presets (save / load / recall / delete) — save/load wire
    encoding NOT yet decoded.
  * Master volume + per-channel mute + per-channel phase invert (live
    verified end-to-end via Scarlett loopback measurements).

USB profile: HID-only.  The DSP-408 is NOT a USB Audio Class device —
audio I/O happens entirely on the analog jacks; the USB interface only
carries control HID frames.
"""

from .config import (
    default_search_paths,
    friendly_name_for,
    load_aliases,
)
from .device import (
    Device,
    DeviceInfo,
    DeviceNotFound,
    ProtocolError,
    enumerate_devices,
    resolve_selector,
)
from .protocol import (
    DIR_CMD,
    DIR_RESP,
    DIR_WRITE,
    DIR_WRITE_ACK,
    FRAME_MAGIC,
    PID,
    VID,
    build_frame,
    category_hint,
    parse_frame,
    xor_checksum,
)

__all__ = [
    "VID",
    "PID",
    "FRAME_MAGIC",
    "DIR_CMD",
    "DIR_RESP",
    "DIR_WRITE",
    "DIR_WRITE_ACK",
    "build_frame",
    "category_hint",
    "parse_frame",
    "xor_checksum",
    "Device",
    "DeviceInfo",
    "DeviceNotFound",
    "ProtocolError",
    "enumerate_devices",
    "resolve_selector",
    "load_aliases",
    "friendly_name_for",
    "default_search_paths",
]

__version__ = "0.1.0"

"""Unit tests for USBIP-bridge-friendly behavior in dsp408.device.

Covers:
  * ``_is_usbip_bridged_path`` — the heuristic for recognizing devices
    attached via the Linux ``vhci-hcd`` virtual USB bus.
  * ``enumerate_devices`` — surfaces ``is_bridged`` per device.
  * ``Device.open(read_pacing_s=...)`` — explicit pacing override.
  * ``Device`` rate-limiter — pacing spaces exchanges as expected.

See commit that added these for the ESP32-bridge context.
"""
from __future__ import annotations

import time
from collections import deque
from unittest.mock import patch

from dsp408 import device as _dev
from dsp408.device import Device, _is_usbip_bridged_path
from dsp408.protocol import (
    CAT_PARAM,
    DIR_WRITE_ACK,
    Frame,
    build_frame,
    parse_frame,
)


# ── path detection ──────────────────────────────────────────────────────
class _FakeOSPath:
    """Stub for os.path.exists used inside _is_usbip_bridged_path."""
    def __init__(self, exists: bool = False):
        self.exists_returns = exists
        self.queried: list[str] = []

    def exists(self, p):
        self.queried.append(p)
        return self.exists_returns


def test_path_detection_empty_path_is_not_bridged():
    assert _is_usbip_bridged_path(b"") is False
    assert _is_usbip_bridged_path(None) is False  # type: ignore[arg-type]


def test_path_detection_local_usb_is_not_bridged():
    # The office DSP on raslabel lives at this path; shouldn't be
    # flagged as bridged.
    assert _is_usbip_bridged_path(b"1-1.2:1.0") is False
    assert _is_usbip_bridged_path(b"1-1:1.0") is False


def test_path_detection_bus_2_prefix_is_bridged():
    """Pragmatic heuristic: bus 2 on a Pi-class system is the vhci-hcd
    virtual bus when ``usbip attach`` has imported a remote device."""
    assert _is_usbip_bridged_path(b"2-1:1.0") is True
    assert _is_usbip_bridged_path(b"2-1.1:1.0") is True


def test_path_detection_hidraw_style_not_bridged():
    """``/dev/hidraw*`` paths don't carry bus info — conservative
    fallback is ``not bridged`` (caller can opt in explicitly)."""
    assert _is_usbip_bridged_path(b"/dev/hidraw0") is False
    assert _is_usbip_bridged_path(b"/dev/hidraw3") is False


def test_path_detection_usb2_suffix_in_path():
    """The libusb-style path sometimes includes ``/usb2/...`` even
    when the top-level prefix is different; catch that too."""
    assert _is_usbip_bridged_path(b"some/prefix/usb2/2-1:1.0") is True


# ── enumerate_devices wiring ────────────────────────────────────────────
def test_enumerate_devices_marks_bridged_field():
    """enumerate_devices() adds an ``is_bridged`` key per device."""
    fake_raw = [
        {
            "path": b"1-1.2:1.0",
            "vendor_id": 0x0483, "product_id": 0x5750,
            "serial_number": "LOCAL123",
            "product_string": "Audio_Equipment",
            "manufacturer_string": "Audio_Equipment",
        },
        {
            "path": b"2-1:1.0",
            "vendor_id": 0x0483, "product_id": 0x5750,
            "serial_number": "BRIDGED456",
            "product_string": "Audio_Equipment",
            "manufacturer_string": "Audio_Equipment",
        },
    ]
    with patch.object(_dev.HidCompat, "enumerate", return_value=fake_raw):
        with patch.object(_dev, "load_aliases", return_value={}):
            devs = _dev.enumerate_devices()
    assert len(devs) == 2
    local = next(d for d in devs if d["serial_number"] == "LOCAL123")
    bridged = next(d for d in devs if d["serial_number"] == "BRIDGED456")
    assert local["is_bridged"] is False
    assert bridged["is_bridged"] is True


# ── Device.open pacing plumbing ─────────────────────────────────────────
class _StubHid:
    """Minimal HidCompat stand-in so Device.open doesn't touch real USB."""
    def __init__(self, *a, **kw): pass
    def open_path(self, path): return self
    def close(self): pass
    def write(self, data): return len(data)
    def read(self, nbytes, timeout_ms=0): return b""


def test_device_open_explicit_pacing_overrides_autodetect(monkeypatch):
    """Passing read_pacing_s=... takes precedence over is_bridged."""
    # Mock enumeration to produce a local-USB device
    local_info = {
        "index": 0,
        "path": b"1-1.2:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "FAKE",
        "product_string": "",
        "manufacturer": "",
        "display_id": "FAKE",
        "is_bridged": False,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [local_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    # Default: no pacing for local USB.  wake=False because the stub
    # HID can't actually round-trip a get_info call.
    d_auto = Device.open(path=b"1-1.2:1.0", wake=False)
    assert d_auto._read_pacing_s == 0.0
    # Explicit override to 0.1s
    d_override = Device.open(path=b"1-1.2:1.0", read_pacing_s=0.1, wake=False)
    assert d_override._read_pacing_s == 0.1


def test_device_open_autodetect_pacing_for_bridged(monkeypatch):
    """Bridged devices default to DEFAULT_BRIDGED_PACING_S."""
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    d = Device.open(path=b"2-1:1.0", settle_s=0.0, wake=False)
    assert d._read_pacing_s == Device.DEFAULT_BRIDGED_PACING_S
    assert d._read_pacing_s > 0


def test_device_open_explicit_zero_disables_pacing_on_bridged(monkeypatch):
    """``read_pacing_s=0.0`` explicitly turns pacing OFF for a bridged
    device — gives the user a knob to test unpaced behaviour."""
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    d = Device.open(path=b"2-1:1.0", read_pacing_s=0.0, settle_s=0.0, wake=False)
    assert d._read_pacing_s == 0.0


def test_device_open_default_settle_is_zero():
    """The default ``DEFAULT_BRIDGED_SETTLE_S`` is now 0 — the kernel-HID
    URB race is handled by the wake-OUT, not by sleeping.  Settle is
    kept as an opt-in knob for callers that want extra slack.
    """
    assert Device.DEFAULT_BRIDGED_SETTLE_S == 0.0


def test_device_open_explicit_settle_still_works(monkeypatch):
    """Even though the default is 0, the settle_s=… override still
    triggers the post-open sleep — for callers that want defensive
    padding before their first real call.
    """
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    slept = []
    import dsp408.device as _d
    orig_sleep = _d.time.sleep
    try:
        _d.time.sleep = lambda s: slept.append(s)
        Device.open(path=b"2-1:1.0", settle_s=0.5, wake=False)
    finally:
        _d.time.sleep = orig_sleep
    assert 0.5 in slept, f"expected explicit settle of 0.5s; got {slept}"


# ── wake-OUT (the actual fix for the kernel-HID-URB race) ─────────────
def test_device_open_calls_wake_by_default(monkeypatch):
    """Device.open() must call _wake_hid() immediately after open_path()
    returns, before settle and before returning the Device.  This
    sends ONE interrupt-OUT (a get_info read) so the kernel HID layer's
    waiting interrupt-IN URB completes before its ~1s timeout fires
    and the kernel re-probes the device.  Critical for USBIP-bridged
    devices; harmless for local.
    """
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    wake_calls: list[int] = []
    orig_wake = Device._wake_hid

    def fake_wake(self):
        wake_calls.append(1)
        # don't actually try to talk to the stub HID
    monkeypatch.setattr(Device, "_wake_hid", fake_wake)
    Device.open(path=b"2-1:1.0")
    assert len(wake_calls) == 1, (
        f"expected exactly one wake call on open; got {len(wake_calls)}"
    )


def test_device_open_skips_wake_when_disabled(monkeypatch):
    """``Device.open(wake=False)`` must NOT call _wake_hid()."""
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    wake_calls: list[int] = []
    monkeypatch.setattr(Device, "_wake_hid", lambda self: wake_calls.append(1))
    Device.open(path=b"2-1:1.0", wake=False)
    assert wake_calls == [], (
        "wake=False must skip _wake_hid; got "
        f"{len(wake_calls)} call(s)"
    )


def test_wake_swallows_exceptions(monkeypatch):
    """_wake_hid() must NEVER propagate — if get_info raises, that
    error should be deferred to the caller's first real call so the
    open path stays clean.
    """
    bridged_info = {
        "index": 0,
        "path": b"2-1:1.0",
        "vid": 0x0483, "pid": 0x5750,
        "serial_number": "BRIDGED",
        "product_string": "",
        "manufacturer": "",
        "display_id": "BRIDGED",
        "is_bridged": True,
    }
    monkeypatch.setattr(_dev, "enumerate_devices", lambda: [bridged_info])
    monkeypatch.setattr(_dev, "HidCompat", _StubHid)
    # Force get_info to blow up
    def boom(self):
        raise OSError("simulated read error")
    monkeypatch.setattr(Device, "get_info", boom)
    # open() must succeed despite get_info raising during wake
    d = Device.open(path=b"2-1:1.0")
    assert d is not None


# ── rate-limiter behaviour ──────────────────────────────────────────────
class _CapturingTransport:
    """Stand-in for transport.Transport that auto-replies so we can
    drive Device._exchange without real hardware."""
    def __init__(self):
        self.sent: list[bytes] = []
        self._queued: deque[Frame] = deque()
        self.hid = self

    def send_frame(self, frame: bytes) -> None:
        self.sent.append(frame)

    def send_frames(self, frames) -> None:
        for f in frames:
            self.sent.append(f)

    def read_response(self, timeout_ms: int = 2000):
        if self._queued:
            return self._queued.popleft()
        # Auto-ack any write so set_channel etc. succeed
        last = self.sent[-1] if self.sent else b""
        f = parse_frame(last)
        if f is None:
            return None
        ack_raw = build_frame(direction=DIR_WRITE_ACK, seq=f.seq,
                              cmd=f.cmd, data=b"", category=f.category)
        return parse_frame(ack_raw)

    def close(self): pass


def test_pacing_does_not_delay_when_disabled():
    """With ``_read_pacing_s=0`` (local-USB default) back-to-back
    exchanges incur zero pacing delay."""
    t = _CapturingTransport()
    d = Device(t, info={}, read_pacing_s=0.0)
    start = time.monotonic()
    for _ in range(5):
        d.set_master(db=0.0, muted=False)
    elapsed = time.monotonic() - start
    assert elapsed < 0.05, (
        f"zero-pacing exchanges took {elapsed:.3f}s — unexpected delay"
    )


def test_pacing_enforces_minimum_interval():
    """With ``_read_pacing_s=0.05`` (bridged default), 5 exchanges
    should take at least 4×pacing seconds (the first is free, then
    each subsequent waits pacing)."""
    t = _CapturingTransport()
    d = Device(t, info={}, read_pacing_s=0.05)
    start = time.monotonic()
    for _ in range(5):
        d.set_master(db=0.0, muted=False)
    elapsed = time.monotonic() - start
    # 4 inter-exchange gaps × 0.05 = 0.2 s minimum; allow generous slack
    # for scheduler jitter.
    assert 0.18 < elapsed < 0.6, (
        f"paced exchanges took {elapsed:.3f}s — expected ~0.2-0.3s"
    )


def test_pacing_respects_natural_idleness():
    """If there's a natural sleep between exchanges, the rate-limiter
    shouldn't add extra delay — that's the whole point, polls at
    poll_interval=2s pay zero pacing overhead per exchange."""
    t = _CapturingTransport()
    d = Device(t, info={}, read_pacing_s=0.05)
    d.set_master(db=0.0, muted=False)
    time.sleep(0.1)  # > pacing interval
    start = time.monotonic()
    d.set_master(db=0.0, muted=False)
    elapsed = time.monotonic() - start
    assert elapsed < 0.02, (
        f"paced exchange after natural idleness took {elapsed:.3f}s"
    )

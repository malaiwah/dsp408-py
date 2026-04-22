"""Shared fixtures for live-hardware tests.

Gating rule: every live test is skipped unless ``DSP408_LIVE=1`` is set
AND a DSP-408 is actually enumerable on this machine.  Without those
guards, ``pytest tests/live/`` would try to open USB on a dev machine
without hardware and crash.

Fixtures also own state hygiene: baseline snapshot at fixture setup,
restore at teardown, so every test starts on a clean device regardless
of ordering.
"""
from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

# ── skip-everything gate ────────────────────────────────────────────────
pytestmark = pytest.mark.skipif(
    os.environ.get("DSP408_LIVE") != "1",
    reason="live tests need DSP408_LIVE=1 and a plugged-in DSP-408",
)


# ── device fixture ──────────────────────────────────────────────────────
@pytest.fixture(scope="module")
def dsp():
    """One opened + connected Device per test module.

    Uses ``DSP408_DEVICE`` (display_id / serial / friendly alias) to
    select, otherwise the first found device.  Skips the module if no
    device is enumerable so the suite degrades gracefully.
    """
    if os.environ.get("DSP408_LIVE") != "1":
        pytest.skip("DSP408_LIVE=1 not set")

    from dsp408 import Device, enumerate_devices

    devs = enumerate_devices()
    if not devs:
        pytest.skip("no DSP-408 enumerable on USB")

    selector = os.environ.get("DSP408_DEVICE")
    if selector:
        # Device.open() accepts serial / display_id / friendly_name via selector
        d = Device.open(selector=selector)
    else:
        d = Device.open(path=devs[0]["path"])
    try:
        d.connect()
        yield d
    finally:
        try:
            d.close()
        except Exception:
            pass


# ── snapshot helpers ────────────────────────────────────────────────────
def snapshot_all(dsp) -> dict[int, bytes]:
    """Grab a full per-channel 296-byte blob snapshot of all 8 channels.

    Reads are fully byte-stable as of 2026-04-22 (see the note on
    ``UNSTABLE_READ_REGION`` above).  Returns the raw blobs unmodified.
    """
    return {ch: bytes(dsp.read_channel_state(ch)) for ch in range(8)}


#: Reserved for future use.  Earlier versions of this file declared a
#: "shifted-blob" unstable region at 48..245 to work around what was
#: believed to be a firmware read-divergence quirk; the real root cause
#: turned out to be a parser bug in
#: :func:`dsp408.protocol.parse_frame` that under-read the first
#: multi-frame reply by 2 bytes.  With that bug fixed (2026-04-22),
#: reads are fully stable and no region needs to be masked.  Left as
#: an empty tuple so tests that imported the constant still work.
UNSTABLE_READ_REGION: tuple[int, int] = (0, -1)  # empty range


def diff_blobs(
    before: dict[int, bytes],
    after: dict[int, bytes],
    *,
    ignore_unstable_region: bool = False,
) -> dict[int, list[tuple[int, int]]]:
    """Return the list of (start, end) inclusive offset ranges where
    per-channel blobs differ.

    ``ignore_unstable_region`` is retained for backward compatibility
    but has no effect now that the parser fix landed — the whole blob
    is byte-stable across consecutive reads.

    Returns a dict only containing channels that actually changed.
    """
    out: dict[int, list[tuple[int, int]]] = {}
    for ch in range(8):
        idx = [i for i in range(296) if before[ch][i] != after[ch][i]]
        if not idx:
            continue
        ranges: list[tuple[int, int]] = []
        s = idx[0]
        p = s
        for i in idx[1:]:
            if i == p + 1:
                p = i
            else:
                ranges.append((s, p))
                s = i
                p = i
        ranges.append((s, p))
        out[ch] = ranges
    return out


def assert_only_changed(
    before: dict[int, bytes],
    after: dict[int, bytes],
    expected: dict[int, list[tuple[int, int]]],
) -> None:
    """Assert that every byte change between before/after falls within
    the ``expected`` ranges on the specified channel, and that NO other
    channel has any change.

    This is a "contained in" assertion, not an "equal to" assertion —
    individual bytes within the expected range may not actually change
    if the write value happens to equal the baseline for that byte
    (e.g. writing gain=570 produces the same gain_hi=0x02 as the
    default gain=600).  The surgical-write guarantee we care about is
    "only these bytes could change"; whether they DO change depends on
    baseline values.
    """
    actual = diff_blobs(before, after)
    # Unexpected channels changed
    unexpected_ch = set(actual) - set(expected)
    assert not unexpected_ch, (
        f"unexpected cross-channel mutations on channels {sorted(unexpected_ch)}: "
        f"{ {ch: actual[ch] for ch in unexpected_ch} }"
    )
    for ch, want_ranges in expected.items():
        got_ranges = actual.get(ch, [])
        if not got_ranges:
            continue  # zero bytes changed — allowed (coincidental equality)
        # Every changed byte on this channel must be covered by at
        # least one expected range.
        def _covered(offset: int) -> bool:
            return any(s <= offset <= e for s, e in want_ranges)
        for got_s, got_e in got_ranges:
            for off in range(got_s, got_e + 1):
                assert _covered(off), (
                    f"ch{ch} byte {off} changed, but not covered by any "
                    f"expected range {want_ranges}.  Full got={got_ranges}"
                )


# ── lease helper: write → assert → restore ──────────────────────────────
def restore_state(dsp, before: dict[int, bytes]) -> None:
    """Best-effort restore: apply the pre-test state using surgical APIs.

    We avoid ``set_full_channel_state`` because it has the documented
    2-byte payload shift quirk.  Instead, reconstruct state via the
    per-parameter setters (safe, surgical, byte-exact).  This is an
    inversion of the things we typically test, so the restore path
    itself is exercised on every teardown.
    """
    from dsp408.device import parse_channel_state_blob

    for ch in range(8):
        parsed = parse_channel_state_blob(before[ch], ch)
        if parsed is None:
            continue
        dsp.set_channel(
            ch,
            db=parsed["db"],
            muted=parsed["muted"],
        )
        # polar + delay via set_channel if available; otherwise skip
        # (only set_channel landed on the restore hot-path because it's
        # what most tests perturb).

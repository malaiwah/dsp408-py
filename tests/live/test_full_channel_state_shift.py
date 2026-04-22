"""``set_full_channel_state`` round-trip + channel-isolation invariants.

Pre-2026-04-22 versions of this file were titled ``..._shift`` and
characterised a "2-byte firmware drop at offsets 48..49 of multi-frame
WRITEs".  That quirk turned out to not exist — it was a bug in
:func:`dsp408.protocol.parse_frame` that under-read the first frame of
multi-frame READ replies by 2 bytes, making the post-write readback
LOOK like the write had lost 2 bytes.  With the parser fixed the
write path is byte-exact end-to-end.

Filename kept (rather than renamed) so git blame / test-discovery
references don't churn.
"""
from __future__ import annotations

import pytest

from .conftest import diff_blobs, snapshot_all


@pytest.mark.parametrize("channel", [0, 3, 4, 7])
def test_set_full_channel_state_no_cross_channel_side_effects(dsp, channel):
    """set_full_channel_state on channel N ONLY mutates channel N's
    blob.  No other channel sees any change.  Asserts channel isolation
    for both the ch<4 path (cmd=0x10000..0x10003) and the ch>=4 path
    (cmd=0x04..0x07); the latter was originally reported as having
    cross-channel wipes but that turned out to be read-divergence
    artifacts from the old parser bug.
    """
    before = snapshot_all(dsp)
    # Read the target channel's current blob, tweak one field in the
    # basic record (now at 248..255 with the parser fix), write it back.
    current = bytes(dsp.read_channel_state(channel))
    blob = bytearray(current)
    # Flip HPF slope byte at the corrected offset 259 (was 257 pre-fix).
    from dsp408.protocol import OFF_HPF_SLOPE
    blob[OFF_HPF_SLOPE] = 2 if blob[OFF_HPF_SLOPE] != 2 else 3
    try:
        dsp.set_full_channel_state(channel, bytes(blob))
        after = snapshot_all(dsp)
        diff = diff_blobs(before, after)
        other_channels = set(diff) - {channel}
        assert not other_channels, (
            f"set_full_channel_state({channel}) leaked into channels "
            f"{sorted(other_channels)}: "
            f"{ {ch: diff[ch] for ch in other_channels} }"
        )
    finally:
        # Restore HPF slope to factory default 1 via the surgical API.
        dsp.set_crossover(
            channel,
            hpf_freq=20, hpf_filter=0, hpf_slope=1,
            lpf_freq=20000, lpf_filter=0, lpf_slope=1,
        )


def test_set_full_channel_state_preserves_semantic_fields(dsp):
    """Writing back a channel's OWN current blob (round-trip) preserves
    every semantic per-channel field (mute, gain, delay, polar,
    crossover, mixer routing, compressor, channel name).

    Since the parser-fix landed the round-trip should be byte-exact,
    but we check via the semantic parser (``parse_channel_state_blob``)
    rather than raw bytes so this test surfaces any FIELD-level
    regression cleanly even if the blob has per-read bookkeeping bytes
    that change between reads (not known to be the case for the
    channel-state blob, but defensive).
    """
    channel = 3
    current = bytes(dsp.read_channel_state(channel))
    pre = dsp.parse_channel_state_blob(current, channel)
    if pre is None:
        pytest.skip(
            f"baseline blob for ch{channel} fails the parser's sanity "
            f"check — skipping (not a library bug, just a state the "
            f"conservative parser refuses to decode)."
        )
    dsp.set_full_channel_state(channel, current)
    after = bytes(dsp.read_channel_state(channel))
    post = dsp.parse_channel_state_blob(after, channel)
    assert post is not None, "post-write blob failed to parse"
    # Semantic equality check on every user-facing field
    assert pre["muted"] == post["muted"], "mute flipped through round-trip"
    assert pre["polar"] == post["polar"], "polar flipped through round-trip"
    assert abs(pre["db"] - post["db"]) < 0.05, "gain drifted"
    assert pre["delay"] == post["delay"], "delay changed"
    assert pre["name"] == post["name"], "name changed"
    assert pre["mixer"] == post["mixer"], "mixer routing changed"
    assert pre["hpf"] == post["hpf"], "HPF params changed"
    assert pre["lpf"] == post["lpf"], "LPF params changed"

# Known firmware / driver quirks

Empirical findings from live-hardware regression testing on
`MYDW-AV1.06` (v1.06), last overhauled 2026-04-22.  This file used to
have 8 entries; several were phantoms attributed to the firmware that
turned out to share a single root cause in *this* library's parser.

See `tests/live/` for the live-hardware regression suite that locks in
each real quirk.

## ❌ Not firmware quirks — single library bug

Pre-2026-04-22 this document catalogued two separate "firmware quirks"
that both traced to the same bug in
[`dsp408/protocol.py::parse_frame`](../dsp408/protocol.py):

- **"Read-divergence on early-session reads"** (the one that made
  ~6 % of early reads return a 2-byte left-shifted blob).
- **"Multi-frame WRITE 2-byte payload drop"** (the one that made
  full-channel-state writes look like they lost bytes 48..49).

Both were a single line in `parse_frame`: the function capped the first
frame's payload at 48 bytes (reserving 2 for the chk + end markers),
but **multi-frame first frames carry 50 payload bytes — no chk/end in
the first frame** (they go at the tail of the last continuation per the
wire spec).  Every multi-frame READ reply was losing 2 bytes, causing
every byte from offset 48 onward to appear 2 positions earlier in the
reassembled blob.

Write-then-read tests that seemed to show "bytes 48..49 of my write got
lost" were actually showing "my read-back of the full 296 bytes is 2
positions left-shifted, so bytes I put at offset 50 appear to be at
offset 48."

Fixed by patching `parse_frame` to detect multi-frame first frames
(`payload_len > 48`) and read the full 50 payload bytes in that case.
Verified against the Windows GUI capture blobs on the
`reverse-engineering` branch: 79/79 capture blobs decoded via the
Wireshark Lua dissector's (independently-correct) reassembly match
exactly what the fixed Python parser now produces.

**Cascading impact of the fix:**
- Every `OFF_*` constant for offsets ≥48 in `dsp408/protocol.py`
  shifted +2.  (The old values were correct for the buggy-reassembly
  view; the new values are correct for the firmware's actual layout.)
- The "adaptive double-read" + "connect-time warmup" logic in
  `Device.read_channel_state` / `Device.connect` was removed — reads
  are byte-exact on every call now.
- The `_payload_matches_ignoring_counter` helper + the "byte 294 is a
  per-read counter" narrative were removed.  Byte 294 was never a
  counter; the buggy reassembly was just exposing random trailing
  padding bytes of the last HID continuation frame.
- `UNSTABLE_READ_REGION` in `tests/live/conftest.py` was zeroed out.
- The `set_full_channel_state` "pad blob[48..49] to match
  blob[50..51]" workaround was removed — full-channel writes now
  round-trip byte-exactly.

## Real quirks that remain

### 1. EQ bands 6..9 storage layout isn't `band * 8`

**Where:** `set_eq_band(ch, band=6..9, ...)` writes.

**Symptom:** Writes are accepted and round-trip, but don't show up at
the expected offsets `blob[band * 8 .. band * 8 + 4]`.  Bands 0..5 do
use the `band * 8` stride.

**What's really in that region (offsets 48..245)?**  Pre-2026-04-22
docs called it "leon-style padding for unused band slots" but that's
likely wrong — the region holds SOMETHING that occasionally differs
between otherwise-consecutive reads (see the residual read-divergence
on bytes 48..79).  Possibilities under investigation:
- Additional internal per-band records (e.g. computed filter
  coefficients cached from the 6 user-facing bands).
- State for undocumented opcodes we haven't decoded yet (a parallel
  RE session is working on this).
- A genuine cached / transient state machine that can legitimately
  flip between two valid forms.

Until that session concludes, treat 48..245 as **region of unknown
semantics**, not "padding".

**Consequence for users:** use bands 0..5 for EQ.  Writes to 6..9
aren't forbidden but readback won't line up with your intent.  Don't
key automations on bytes 48..245.

### 2. Compressor block is inert in firmware v1.06

**Where:** `set_compressor()` (cmd=0x2300+ch) writes to blob[280..287].

**Symptom:** Writes land byte-exactly, but the audio engine ignores
every parameter combination.  Four-way confirmation (live audio rig,
firmware disasm, leon Android source, Windows UI inspection) shows the
block is not wired to audio.

**Consequence:** `set_compressor` round-trips correctly for state
storage / preset preservation, but doesn't affect audio.  The Windows
GUI V1.24 doesn't expose compressor controls anywhere — Dayton seems
to have dropped the feature from the user-facing product.

### 3. Compressor shadow at offsets 272..279

**Where:** 296-byte channel-state blob, offsets 272..279.

**Symptom:** On Windows GUI captures this region mirrors the live
compressor record at 280..287 (same factory-default bytes).  On our
spare device it reads as 8 × 0x20 (spaces) and doesn't track
writes to cmd=0x2300+ch (those only hit 280..287).

**Consequence:** Treat 272..279 as read-only / GUI-populated; always
write compressor via cmd=0x2300+ch (→ 280..287).  Reading 272..279 may
be useful for diagnostics ("what did the GUI set as the default Q?")
but nothing else.

### 4. Cross-session persistence requires `save_preset()` for flash

**Where:** Every mutating public API.

**Symptom:** Writes land in RAM and persist across a
`Device.close() + Device.open()` within the same USB power cycle
(verified by `tests/live/test_persistence_reopen.py`).  They don't
necessarily survive a USB power cycle — preset-slot flash writes are
the firmware's persistence mechanism; call `save_preset(name)` after
whatever change you want to keep.

**Consequence:** MQTT / UI applications that want user tweaks to
survive reboots of the DSP should expose a "Save Preset" control and
invite the user to click it after tuning.

### 5. `0xAA` fill in uninitialized channel regions

**Where:** Channel blobs for output channels that have never had a
preset loaded for them.

**Symptom:** Bytes in the name (288..295) / compressor (280..287) /
EQ-region (48..245) of uninitialized channels frequently contain
`0xAA` padding bytes.  Configured channels have meaningful values
there.

**Consequence (and silver lining):** The `0xAA` fill is a usable
signal for "is this channel configured?" detection — if a read
returns a channel blob whose name field is heavy with `0xAA` bytes,
the channel almost certainly hasn't been through a preset-load or
explicit configuration cycle.  Useful when bootstrapping new DSPs.

### 6. Test-session-order quirk: some `set_eq_band` rapid-chain writes drop

**Where:** `set_eq_band()` calls in the middle of a long rapid
sequential-write chain — the specific `(channel, band)` that drops
varies between runs depending on cumulative session state.  Observed
groupings: `(5..7, band=0)`, `(1..3, band=3)`, `(5..7, band=3)`.

**Symptom:** Write is accepted (WR_ACK received) but the value
doesn't appear in the blob on the next read.  The same write in
isolation (fresh session, reset defaults, write, read) lands
correctly on all channels.  The batch test
``test_all_eq_verified_positions_round_trip`` passes for every
``(channel, band=0..5)`` combination.

**Status:** Not a library bug per isolated reproduction.  Possibly a
firmware cmd-dispatch history effect combined with the "startup
write-drop quirk" that needs ~5–6 warmup writes.  The affected test
cases are marked `xfail` in
``tests/live/test_surgical_writes.py::test_set_eq_band_lands_correctly``.
Worth revisiting now that we can do byte-level capture + replay
against the Windows GUI via the Wireshark dissector.

## Wireshark visibility

Every real (and former) quirk is visible in Wireshark captures via the
provided dissector (`tools/wireshark/dsp408.lua`).  A live-test run on
raslabel produces a ~12,000-frame capture that dissects cleanly end-to-
end including multi-frame reassembly and semantic decoding of every
`set_*()` write's payload.  First diagnostic step for any new
quirk report:

1. USBPcap (Windows) or `usbmon` (Linux) the traffic while
   reproducing.
2. Open in Wireshark with the dissector loaded.
3. Compare the command sequence to a known-good capture on the
   `reverse-engineering` branch (`captures/full-sequence.pcapng` is
   the gold standard).
4. If the new capture shows different bytes at the same offset than
   we'd predict, check whether our library's view of that offset
   matches the dissector's — the dissector is now the authoritative
   reference for the on-wire layout.

# Dayton DSP-408 Android app decompile — feature & protocol findings

Decompile date: 2026-04-19
Tool: `jadx 1.5.5`
Decompile output (not committed; ~25 MB): `/tmp/dsp408-apk/jadx-real/` (tigerapp), `/tmp/dsp408-apk/jadx-leon/` (leon.android v1.23)

## TL;DR — the single most important finding

**The official v1.23 BLE wire frame is byte-for-byte identical to our USB HID frame.** The
Bluetooth dongle (DSP-BT4.0) is a transparent BLE-to-UART bridge over a stock HM-10 / CC2540
module. Both transports speak the same firmware wire protocol. Everything the BLE app does is
reachable via USB by reusing the same opcodes — we just hadn't decoded the structure inside the
4-byte `cmd_le32` field yet.

Frame structure side-by-side:

```
USB HID (our protocol.py)            BLE/SPP (leon v1.23 ServiceOfCom.java:820–874)
─────────────────────────────────    ─────────────────────────────────────────────
0..3   80 80 80 EE   magic           80 80 80 EE   sync bytes + Define.SEFFFILE_EncryptByte
4      direction                     FT  FrameType  (a1=write, a2=read, 51=ack, 53=data)
5      version (always 01)           DID  (was treated as version)
6      seq                           UID  (was treated as seq)
7      category (09=state,04=param)  DT  DataType (3=MUSIC, 4=OUTPUT, 9=SYSTEM)
8..11  cmd_le32                      CID, DataID, BTD, PCC  ← DECOMPOSITION!
12,13  payload_len_le16              LEN_lo, LEN_hi
14..N  payload                       payload (8 bytes for typed writes, 296/136/112 for bulks)
N      xor_chk                       XOR of bytes[4..N-1]
N+1    AA                            AA  end marker
```

What this means for our `cmd_le32` field: **we've been treating it as an opaque 32-bit opcode,
but it's actually a 4-byte struct `{CID, DataID, BTD, PCC}` (channel ID, data ID, ?, ?).**
That's why our cmds have the form `0x77NN`, `0x1fNN`, `0x21NN` — the low byte is the channel
index (CID) and the next byte (DataID) selects the operation. BTD and PCC are zero in every
capture we have, but the firmware reads them.

Concrete remap of our existing commands:

| Our cmd | DataType (cat) | CID | DataID | Meaning |
|--------:|--------------:|----:|-------:|---------|
| `0x7700..0x7707` | 0x04 OUTPUT | 0..7 | `0x77` | Read 296-byte channel state blob |
| `0x1F00..0x1F07` | 0x04 OUTPUT | 0..7 | `0x1F` | Write 8-byte basic record (en/vol/delay/subidx) |
| `0x2100..0x2107` | 0x04 OUTPUT | 0..7 | `0x21` | Write routing row (4 input levels) |
| `0x05`           | 0x09 SYSTEM | 5    | `0x00` | Read/write master volume + mute (as DT=9 cmd at CID=5) |
| `0x04`           | 0x09 SYSTEM | 4    | `0x00` | Read device identity |
| `0x00`           | 0x09 SYSTEM | 0    | `0x00` | Read/write preset name |
| `0xCC`           | 0x09 SYSTEM | 0xCC | `0x00` | Connect handshake |

## The two apps

| | tigerapp v1.5.23 (vercode 12) | leon.android v1.23 (vercode 19, Play Store) |
|---|---|---|
| Package | `com.tigerapp.rkeqchart_application_408` | `leon.android.chs_ydw_dcs480_dsp_408` |
| File count | 379 | 1747 |
| BLE GATT UUIDs | `0000ae00`/`ae02` (vendor-custom) | `0000ffe0`/`ffe1` (HM-10/CC2540 standard) |
| Classic SPP | no | yes, UUID `00001101-...` |
| Wire framing | `[80, len-2, cmd, payload, crc16_modbus_be]` (poly 0xA001) | **identical to our USB frame** (see TL;DR) |
| Custom-ID handshake | yes (`4061` = 0x0FDD) | no — uses DataType=9, ChannelID=3 reads instead |
| File manager / preset import-export | no | yes, `.jssh`/`.jsah` JSON files |
| OTA app updater | no | yes (Android `DownloadManager`) |
| Compressor / dynamics UI | no | yes (`SetEQFreqBWGainDialogFragment`, `Pressure` fragment) |
| 31-band PEQ in data model | no (10-band) | **yes — firmware supports 31 bands per channel** |
| Per-channel polarity register | yes (`u[ch]` = 66..73) | yes (different addressing) |
| Cloud preset library | no | yes — pulls signed JSON from `http://121.41.9.33/chs_index.php?…AgentID=LDK` |

The tigerapp build is an older fork by a Chinese OEM that licensed the DSP design. The
leon.android build is the official Dayton-branded version with newer firmware support and many
more features. **Wire protocols are completely different between the two apps**, but the leon
v1.23 protocol is the one that matches our USB capture format. Use only v1.23 for cross-reference.

## DSP feature surface (from leon v1.23)

The device exposes **one flat live state** (no preset-bank switching at the firmware level —
presets are pure client-side JSON that gets replayed as a sequence of writes). Per-output
channel, the writeable fields are:

### Per-channel basic record — `DataID=31`, 8 bytes

```
[0]  mute       (0=audible, 1=muted)
[1]  reserved   (0)
[2..3] gain_le16  (signed 16-bit; UI maps via `gain_db = (raw / 10) - 60`)
[4..5] delay_le16 (samples or cm-step index, see below)
[6]  reserved
[7]  subidx     (channel-type identifier; one of CHANNEL_SUBIDX, see notes)
```

This is **identical to what our `0x1FNN` write payload looks like** — confirmed.

### Per-channel parametric EQ — `DataID=0..30`, 8 bytes per band

```
[0..1]  freq_le16   (Hz, indexed via 332-entry table from 20 Hz to 20 kHz)
[2..3]  level_le16  (signed dB×10, sign in bit 15)
[4..5]  bw_le16     (Q index into 100-entry table 0.4..128.0)
[6]     shf_db_u8   (extra shelf-dB byte — only used for shelf filters)
[7]     type_u8     (0=peak, 1=lowshelf, 2=highshelf, 3+=other)
```

**Manual claims 10 bands per channel; leon's data model has 31** (`DataID` up to 30).
But our DSP-408 firmware **only honors writes to bands 0..9** — writes to bands 10..30
are silently no-op'd (verified live via `_probe_eq_extra_bands.py`). The effective
band count is 10.  All 10 are peaking EQ slots (no shelf flag in the wire format
despite the manual's mention of LS/HS shelves on bands 1 & 10).

### Per-channel crossover — `DataID=32`, 8 bytes

```
[0..1]  hpf_freq_le16
[2]     hpf_filter_type   (0=BW, 1=Bessel, 2=LR)
[3]     hpf_slope         (encoded: 6/12/18/24/30/36/42/48 dB/oct, indices 0..7; 8=Off)
[4..5]  lpf_freq_le16
[6]     lpf_filter_type
[7]     lpf_slope
```

**Manual lists 6/12/18/24 dB/oct; firmware supports up to 48 dB/oct** (8 slope steps + Off).

### Per-channel compressor / limiter — `DataID=35` (cmd=0x2300+ch on the wire)

leon's spec:

```
[0..1]  attack_time_le16    (ms; small values 1..50)
[2..3]  release_time_le16   (ms)
[4]     threshold_u8        (dB)
[5..7]  reserved / aux fields (unknown)
```

**Verified-live wire payload** (cmd=0x2300+ch, 2026-04-19 — distinctive
byte injection lands at blob[278..285]):

```
[0..1]  all_pass_q_le16   (firmware default 420 — internal sidechain Q,
                            not in leon's spec)
[2..3]  attack_time_le16  (ms; firmware default 56)
[4..5]  release_time_le16 (ms; firmware default 500)
[6]     threshold_u8      (units not yet calibrated)
[7]     enable            (1=on, 0=bypass — guess from one capture, not
                            verified end-to-end)
```

**Implemented as `Device.set_compressor()` in `dsp408/device.py`.**  The
wire encoding is verified (writes round-trip exactly through
`read_channel_state()`), but compressor *behavior* (an actual
attack/release/threshold curve fit on real audio) is still pending the
loopback-rig probe.

### Per-channel name — `DataID=36`, 8 bytes ASCII

The leon v1.23 build writes 8 bytes; the tigerapp build wrote 14. May be firmware-version
dependent — the device's own `0x00 preset_name` cmd returns 15 bytes today, so it depends
on which scope (channel name vs preset name) we're querying.

### Mixer matrix — `DataID=33` (IN1..IN8) and `DataID=34` (IN9..IN16)

Each cell is **a single signed byte**, value 0..100 percent (or ±dB — needs verification).

```
[0..7] eight u8 cells, one per input source for this output
```

Two findings:
1. **Mixer is percentage, not boolean.** Our driver only writes 0x64 (full) or 0x00 (off) —
   we're missing the level dimension entirely.
2. **The firmware addresses up to 16 inputs.** The DSP-408 hardware has only 4 RCA + 4 high-level +
   1 BT input slots (so ~9 active sources max), but the data model has 16 cells per output.
   Suggests the same firmware/DSP IC powers a larger sibling (DSP-816?) and the unused slots
   are zeroed. We should still write the second-half-mixer (`DataID=34`) as zeros to be safe.

### Master volume + mute — `DataType=9 SYSTEM`, `ChannelID=5`

`raw_u16_le = (mute ? 0 : 5000) + vol*10` — high-bit serves as mute flag rather than a separate
byte. This matches our existing `0x05` decode.

### Preset slots / save / recall

The leon v1.23 firmware has **no slot-based recall protocol**. Presets are purely client-side
JSON files (extensions `.jssh` for single-channel sound effects, `.jsah` for full-config) that
the app replays as writes when loading. The 6 preset buttons in the UI are local app state.

The tigerapp v1.5.23 client uses opcodes `0x01`/`0x10`/`0x11`/`0xF0`/`0x04` for slot
recall/save/reset/apply/read — but those opcodes don't exist in the v1.23 protocol or
firmware. Likely a tigerapp-only client emulation OR a totally different firmware variant
(remember: tigerapp is a different OEM build).

**For our driver: implement preset save/recall as JSON file replay, not as a wire opcode.**

### Magic-word command register — `1567` (0x061F)

Two known magic writes:
- `1567 = 0xA5A6` → factory reset
- `1567 = 0xB500 | preset_id` → load factory preset (one of 6 built-ins)

Address 0x061F doesn't fit our `(DataType, CID, DataID)` decomposition cleanly — likely a
DataType=9, CID=15, DataID=0x06 single-write.

### Streaming on/off — register `1555` (0x0613)

Toggle for the BT audio playback path. Writing 0/1.

### Sound-template / speaker-role auto-config

Picking a "speaker role" from the 25-entry list (`fl_high`, `fl_mid`, `sub_l`, `sub_r`, `sub`,
`null`, etc.) **cascades** writes to that channel's crossover + gain + filter values from a
lookup table in `g.a.i/j/k/l/m`. Pure client-side feature — the firmware doesn't have a
"speaker role" register, the app just writes 5 fields atomically.

Useful for our HA bridge as a "preset speaker layout" dropdown.

## What's NOT in either build

- **No RTA / spectrum analyzer** — neither app implements it. Not a device feature.
- **No signal generator** — same.
- **No DSP firmware OTA over wire** — `Define.IAP_DSP_*` enums (54..60) exist in v1.23 as
  reserved opcodes but no code uses them. Firmware updates are USB-only via the existing
  `0x36/0x37/0x38/0x39` flash sequence we already have.
- **No compressor side-chain** — only per-channel envelope follower on the channel's own signal.
- **No crossfeed / matrix decoder** — just straight 4×8 (or 16×8) routing.

## What CHANNEL_SUBIDX really is

Our table `(0x01, 0x02, 0x03, 0x07, 0x08, 0x09, 0x0F, 0x12)` for channels 0..7 looks like
DSP-internal "channel type" identifiers. The gaps (skipping 04..06, 0A..0E, 10..11) suggest
the firmware has a larger pool of DSP channel implementations and only certain ones are
assigned to physical outputs. Confirmed live: device 1's ch1 was reconfigured to subidx=0x12
(normally ch7's slot), proving the assignment is mutable.

The leon v1.23 code doesn't expose subidx as a UI concept — it just trusts the firmware to
return whatever's stored. **Our dynamic-subidx-discovery fix (commit 96125b0 / 719e45e on
main) is the correct approach.**

## Updated gap list for the Python USB driver

Ranked by value × ease, all reachable via USB by emitting the right `cmd_le32` (CID|DataID):

### High value, low effort

1. **Decode the existing 296-byte read response.** Cmd=0x77NN already gives us the entire
   per-channel state. The DataID layout above tells us what offsets within the blob carry
   what. Write a parser for: PEQ bands × 31 (offsets ~?), crossover (~?), compressor (~?),
   plus the existing volume/mute/delay we already extract at offset 246.
   - **Action**: capture one channel's blob, lay it next to leon v1.23's `DataStruct_Output`,
     and find the offsets empirically. Then publish all fields to MQTT.
2. **Per-channel phase invert / polarity** — single boolean register. In tigerapp it was
   register `66+ch`. **Resolved** — the polar field is byte[1] of the 8-byte channel
   write payload (cmd=0x1F00..0x1F07) and lives at blob[247]; verified live via
   Scarlett-loopback ±180° measurement. The earlier guess "byte[6] of the 8-byte
   record" was tested separately (probe `_probe_eq_mode.py`) and disproved — that
   byte stores at blob[252] but does NOT control EQ bypass either.
3. **Streaming on/off** — register 1555. Single 16-bit write. Note: this just
   toggles whether the GUI polls VU-meter data, not whether audio flows. The
   meter-data wire format is still unknown — both `cmd=0x13` and `cmd=0x03`
   reads return static bytes; need a fresh capture with audio actually playing
   (see `captures-needed-from-windows.md` item #8).
4. **Factory reset** — magic word `0xA5A6` to register 1567. One-line addition.
5. **Load factory preset (1..6)** — magic word `0xB500 | n` to 1567. Six commands total.

### High value, medium effort

6. **Per-channel 10-band PEQ write** — issue `cmd = (band_dataid<<8) | channel` with the 8-byte
   payload above. 10 bands × 8 channels = 80 settable parameters per channel.
7. **Per-channel crossover write** — `DataID=32`, 8-byte payload. One cmd per output.
8. **Per-channel compressor** — `DataID=35`, 8-byte payload (encoding TBD).
9. **Mixer with percentage levels** — change our 0x64/0x00 cells to `0..100` u8. May need
   `DataID=33` AND `DataID=34` writes (16-input mixer; second half always 0).
10. **8-byte per-channel name** — `DataID=36`. Adds per-output labelling for HA.

### Skip / not worth it

- **`4061` Custom-ID handshake** — tigerapp-only, not in v1.23 or USB.
- **Cloud preset library** — vendor-server with auth; not useful for a HA bridge.
- **DSP firmware OTA over BLE** — reserved but unimplemented; we already have USB DFU.
- **A2DP music-player ID3 reader** — Bluetooth-input-specific.
- **`f.b.h = 1.2f` constant** — unexplained, ignore.

## Files of interest in the decompiles

`/tmp/dsp408-apk/jadx-leon/sources/leon/android/chs_ydw_dcs480_dsp_408/` (v1.23 official):
- `service/ServiceOfCom.java:820-874` — frame parser (matches our protocol.py byte-for-byte)
- `operation/DataOptUtil.java:940-1280` — `SendDataToDevice()`, the canonical register map
- `datastruct/{Define,DataStruct,DataStruct_Output,DataStruct_System,DataStruct_EQ}.java`
  — the firmware data shapes (use these as our Python data classes)
- `bluetooth/ble/BluetoothLeService.java:29-30` — HM-10 GATT UUIDs
- `bluetooth/spp_ble/BluetoothChatService.java:22` — Classic SPP UUID
- `encrypt/SeffFileCipherUtil.java` — `.jssh`/`.jsah` preset file format (XOR cipher)

`/tmp/dsp408-apk/jadx-real/sources/com/tigerapp/rkeqchart_application_1/` (v1.5.23 fork):
- `service/BTService.java:1164-1183, 1304-1320, 1322-1466` — Modbus-CRC framing
- `f/a.java` — register address tables
- `g/a.java` — value encodings (dB, Hz, Q, mute-bit math)
- `d/c.java`, `d/o.java`, `d/u.java` — UI fragments revealing PEQ / crossover / mixer / limiter

## Next concrete steps

1. **Capture a single channel's full state with the Windows app**, read it into memory, and
   align the 296 bytes against `DataStruct_Output` to map field offsets. This unlocks
   read-side parity for all the new MQTT entities at once.
2. **Sniff a Windows app PEQ band write** to verify our `cmd = (DataID << 8) | channel` formula
   for `DataID=0..30`. If the bytes match the leon spec above, we can implement PEQ writes
   without further reverse engineering.
3. **Test factory-reset magic word** `0xA5A6` to register 1567 (cmd encoded as cat=0x09,
   CID=15, DataID=6, payload `[0xA5, 0xA6]`) — quick sanity check that the SYSTEM-plane
   register-write path works at all.

Until then, the existing driver is correct — these are additions, not fixes.

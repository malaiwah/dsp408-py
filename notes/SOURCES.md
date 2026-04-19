# Source materials for the reverse-engineering work

This branch contains decoded protocol notes, USB packet captures, and
RE tooling — but **not** the underlying Dayton-copyrighted artifacts
that informed the work. Those need to be obtained from upstream.

## Firmware binary

- **File:** `DSP-408-Firmware-V6.21.bin` (also distributed as
  `DSP-408-Firmware.bin`)
- **Size:** 70,296 bytes
- **Source:** [Dayton Audio's DSP-408 product page]
  (https://www.daytonaudio.com/product/1330/dsp-408-4-x-8-dsp-digital-signal-processor-for-home-and-car-audio)
  — under the "Resources" / "Downloads" section.
- **Used for:** firmware disassembly to map cmd handlers + verify
  decoded blob layouts against silicon reality. Disassembly scripts
  in this repo (`disasm*.py`) read this binary from a local path you
  supply.

## Windows control application

- **File:** `DSP-408-Windows-V1.24.zip`
- **Size:** 3,682,746 bytes
- **Source:** Same Dayton product page, "Downloads" section.
- **Used for:** USBPcap captures of the official GUI's wire output
  (`captures/windows-*.pcapng`). Decoding any future Windows-side
  feature requires running this app under USBPcap.

## Android applications (decompiled)

Two apps decompile the same firmware family (per
`notes/android-app-decompile-2026-04-19.md`):

- **leon.android.chs_ydw_dcs480_dsp_408 v1.23** (Play Store):
  the official Dayton-branded build. Its BLE wire frame is
  **byte-for-byte identical** to our USB HID frame, so it serves as
  the canonical protocol reference. Pull from Google Play (or any
  mirror) and decompile with [jadx](https://github.com/skylot/jadx).
- **com.tigerapp.rkeqchart_application_408 v1.5.23**: an older OEM
  fork using a different (Modbus-CRC) wire format. Less useful, but
  cross-references confirm which features were planned across the
  family vs. only in one variant.

The decompiled sources live at `/tmp/dsp408-apk/jadx-leon/` and
`/tmp/dsp408-apk/jadx-real/` on the original analyst's machine
(~25 MB each). Notes in this branch reference specific files +
line numbers (e.g. `ServiceOfCom.java:820-874`) without
reproducing source code.

## Manual

- **File:** `DSP-408-manual.pdf` (1,263,048 bytes)
- **Source:** Dayton product page, "Manuals" section.
- **Used for:** verifying user-visible feature claims against the
  actual firmware behavior. (Several claims — LS/HS shelf bands,
  6/12/18/24 dB/oct slopes only — turned out to differ from what
  the firmware actually does or accepts.)

## Hardware

- One **Dayton Audio DSP-408** (`MYDW-AV1.06` firmware), purchased.
- One **Focusrite Scarlett 2i2 4th gen** for the audio loopback rig
  (`tests/loopback/` on the `loopback-rig` branch of dsp408-py).

## Why these aren't checked in

Firmware binaries, the Windows installer, the Android APKs, and the
manual PDF are all Dayton's copyrighted work. Disassembly /
decompilation for the limited purpose of writing an interoperable
driver is well-established as fair use under US law (DMCA §1201(f),
Sega v. Accolade) and EU law (Software Directive 2009/24/EC Art. 6).
Redistribution of the underlying copyrighted artifacts is not. So
this public branch carries the *output* of the analysis (notes,
descriptions, our own RE tooling) but points at upstream for the
*input*.

A private mirror including the binary artifacts is maintained
separately for reproducibility, available only to direct
collaborators on the project.

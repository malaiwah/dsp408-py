"""dsp408.cli — command-line tool for live DSP-408 experiments.

Usage:
    dsp408 list                     # enumerate attached DSP-408s
    dsp408 info                     # connect + GET_INFO + preset name
    dsp408 snapshot                 # full startup handshake dump
    dsp408 read <cmd_hex> [--cat 09|04]
    dsp408 read-channel <0..7>
    dsp408 write <cmd_hex> <hex_payload> [--cat 09|04]
    dsp408 write-param <ch> <sub_idx_hex> <value_int>
    dsp408 poll [--interval 1.0]    # spam idle_poll + print state_0x13
    dsp408 flash <firmware.bin>
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import Device, DeviceNotFound, ProtocolError
from .flasher import flash_firmware
from .protocol import CAT_PARAM, CAT_STATE


def _category_hint(cmd: int) -> int:
    """Pick the right category byte for a given cmd (mirrors UI auto-mode)."""
    if 0x7700 <= cmd <= 0x77FF:
        return CAT_PARAM
    if 0x1F00 <= cmd <= 0x1FFF:
        return CAT_PARAM
    if cmd == 0x2000:
        return CAT_PARAM
    return CAT_STATE


def _resolve_category(cmd: int, cat_str: str) -> int:
    s = (cat_str or "").strip().lower()
    if s in ("", "auto"):
        return _category_hint(cmd)
    return int(s, 16)


def _p(label: str, value) -> None:
    print(f"  {label:<16} {value}")


def cmd_list(_args) -> int:
    devs = Device.enumerate()
    if not devs:
        print("(no DSP-408 found)")
        return 1
    for d in devs:
        print(d.get("product_string", "?"))
        _p("vendor", hex(d.get("vendor_id", 0)))
        _p("product", hex(d.get("product_id", 0)))
        _p("serial", d.get("serial_number", ""))
        _p("path", d.get("path", b"").decode("utf-8", errors="replace"))
    return 0


def cmd_info(_args) -> int:
    with Device.open() as dev:
        status = dev.connect()
        identity = dev.get_info()
        preset = dev.read_preset_name()
    print(f"CONNECT status: 0x{status:02x}")
    print(f"GET_INFO:       {identity!r}")
    print(f"Preset name:    {preset!r}")
    return 0


def cmd_snapshot(_args) -> int:
    with Device.open() as dev:
        info = dev.snapshot()
    _p("identity",    info.identity)
    _p("preset name", info.preset_name)
    _p("status byte", f"0x{info.status_byte:02x}")
    _p("state 0x13",  info.state_13.hex(" "))
    _p("global 0x02", info.global_02.hex(" "))
    _p("global 0x05", info.global_05.hex(" "))
    _p("global 0x06", info.global_06.hex(" "))
    return 0


def cmd_read(args) -> int:
    cmd = int(args.cmd_hex, 16)
    cat = _resolve_category(cmd, args.cat)
    with Device.open() as dev:
        dev.connect()
        reply = dev.read_raw(cmd=cmd, category=cat, timeout_ms=3000)
    print(f"cmd=0x{reply.cmd:04x} cat=0x{reply.category:02x} "
          f"dir=0x{reply.direction:02x} seq={reply.seq} "
          f"len={reply.payload_len} chk_ok={reply.checksum_ok}")
    print(f"payload ({len(reply.payload)} bytes):")
    print(reply.payload.hex(" "))
    return 0


def cmd_read_channel(args) -> int:
    ch = int(args.channel)
    with Device.open() as dev:
        dev.connect()
        data = dev.read_channel_state(ch)
    print(f"channel {ch}: {len(data)} bytes")
    print(data.hex(" "))
    return 0


def cmd_write(args) -> int:
    cmd = int(args.cmd_hex, 16)
    cat = _resolve_category(cmd, args.cat)
    payload = bytes.fromhex(args.hex_payload.replace(" ", ""))
    with Device.open() as dev:
        dev.connect()
        reply = dev.write_raw(cmd=cmd, data=payload, category=cat)
    print(f"ack dir=0x{reply.direction:02x} cat=0x{reply.category:02x} "
          f"seq={reply.seq} len={reply.payload_len}")
    return 0


def cmd_write_param(args) -> int:
    ch = int(args.channel)
    sub = int(args.sub_idx_hex, 16)
    val = int(args.value)
    with Device.open() as dev:
        dev.connect()
        dev.write_channel_param(channel=ch, value=val, sub_index=sub)
    print(f"wrote ch={ch} sub=0x{sub:02x} value={val}")
    return 0


def cmd_poll(args) -> int:
    interval = float(args.interval)
    with Device.open() as dev:
        dev.connect()
        try:
            while True:
                state = dev.read_state_0x13()
                preset = dev.read_preset_name()
                print(f"{time.strftime('%H:%M:%S')}  preset={preset!r:16}  "
                      f"state_0x13={state.hex(' ')}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print()
    return 0


def cmd_flash(args) -> int:
    fw = Path(args.firmware)
    if not fw.exists():
        print(f"firmware not found: {fw}", file=sys.stderr)
        return 1

    def progress(cur: int, total: int, label: str) -> None:
        if total:
            pct = cur * 100 // total
            print(f"\r  [{label:>22}] {cur}/{total} ({pct}%)", end="", flush=True)
        else:
            print(f"\r  [{label}]", end="", flush=True)

    try:
        flash_firmware(fw, progress=progress)
    finally:
        print()
    print("Flash complete. Unplug and replug after reboot (~20 s).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dsp408")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="enumerate attached DSP-408s").set_defaults(
        func=cmd_list
    )
    sub.add_parser("info", help="connect + GET_INFO + preset name").set_defaults(
        func=cmd_info
    )
    sub.add_parser("snapshot", help="full startup handshake dump").set_defaults(
        func=cmd_snapshot
    )

    p = sub.add_parser("read", help="raw READ by command code")
    p.add_argument("cmd_hex", help="command code, e.g. 0x04 or 7700")
    p.add_argument("--cat", default="auto",
                   help="category byte hex (auto|09=state|04=param)")
    p.set_defaults(func=cmd_read)

    p = sub.add_parser("read-channel", help="read 296-byte channel state")
    p.add_argument("channel", type=int, help="channel 0..7")
    p.set_defaults(func=cmd_read_channel)

    p = sub.add_parser("write", help="raw WRITE by command code")
    p.add_argument("cmd_hex", help="command code, e.g. 1f07")
    p.add_argument("hex_payload", help="payload bytes, e.g. '01 00 96 01 00 00 00 12'")
    p.add_argument("--cat", default="auto",
                   help="category byte hex (auto|04=param|09=state)")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("write-param", help="write one channel parameter")
    p.add_argument("channel", type=int, help="channel 0..7")
    p.add_argument("sub_idx_hex", help="sub-index hex, e.g. 0x12")
    p.add_argument("value", type=int, help="u32 value")
    p.set_defaults(func=cmd_write_param)

    p = sub.add_parser("poll", help="print preset name + state_0x13 on a loop")
    p.add_argument("--interval", default="1.0", help="seconds between polls")
    p.set_defaults(func=cmd_poll)

    p = sub.add_parser("flash", help="flash a .bin firmware image")
    p.add_argument("firmware", help="path to .bin")
    p.set_defaults(func=cmd_flash)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except DeviceNotFound as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except ProtocolError as e:
        print(f"protocol error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

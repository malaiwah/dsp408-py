"""dsp408.jssh — encode/decode the leon Android app's `.jssh`/`.jsah` preset files.

The leon Android v1.23 app stores presets as JSON files under custom
extensions:
  * `.jssh` — single-channel sound-effect preset
  * `.jsah` — full-config preset (all 8 outputs + globals)

The JSON content is XOR-ciphered with a position-keyed scheme: each
byte is XORed with its position (mod 32768) within the file. The
cipher is symmetric — `decode` and `encode` perform the same
operation. There is no key, no header, no IV.

Source: leon `encrypt/SeffFileCipherUtil.java:14-49`.

Quick example::

    from dsp408 import jssh
    with open("MyPreset.jssh", "rb") as f:
        cipher = f.read()
    plain = jssh.decode(cipher)
    import json
    preset = json.loads(plain)

The decoded JSON schema is leon-internal and not stable; treat it as
opaque per-version state for round-trip purposes.
"""
from __future__ import annotations

# Per leon source: position-XOR is reset every 32768 bytes (32 KB pages)
PAGE_SIZE = 32768


def _xor_in_place(data: bytearray) -> None:
    """Apply the position XOR (modulo PAGE_SIZE) to the buffer in place.

    leon's loop is `for i in 0..32767: data[i] ^= i & 0xff`. For files
    larger than 32 KB the position counter wraps (per
    SeffFileCipherUtil's outer loop over chunked pages).
    """
    n = len(data)
    for i in range(n):
        data[i] ^= (i % PAGE_SIZE) & 0xFF


def decode(cipher_bytes: bytes) -> bytes:
    """Decrypt a .jssh / .jsah file's bytes back to plaintext JSON.

    Operation is symmetric — passing ``decode(decode(x))`` returns ``x``.
    """
    buf = bytearray(cipher_bytes)
    _xor_in_place(buf)
    return bytes(buf)


def encode(plain_bytes: bytes) -> bytes:
    """Encrypt plaintext JSON bytes into the .jssh / .jsah on-disk format.

    Same operation as :func:`decode`; provided as a separate name for
    clarity at call sites.
    """
    buf = bytearray(plain_bytes)
    _xor_in_place(buf)
    return bytes(buf)

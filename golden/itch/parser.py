"""ITCH 5.0 framing, decoding and encoding.

Nasdaq's sample files are a stream of messages, each preceded by a
2-byte big-endian length. The length does not count itself.

    [len hi][len lo][message bytes ...][len hi][len lo][message ...]

decode() turns one message into a dict of field values. encode() does
the reverse, and is used to build test vectors.

The parser is strict on purpose: an unknown type or a length that
disagrees with the spec raises an error. For a golden model, silently
skipping bad input is worse than stopping.
"""

import gzip

from .spec import ALPHA, MESSAGES


class ItchError(ValueError):
    pass


def open_itch(path):
    """Open a raw or gzip-compressed ITCH file for binary reading."""
    with open(path, "rb") as f:
        magic = f.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(path, "rb")
    return open(path, "rb")


def read_frames(stream, chunk_size=1 << 20):
    """Yield each message (without its length prefix) from a binary stream."""
    buf = b""
    pos = 0
    offset = 0  # file offset of buf[0], for error messages
    while True:
        chunk = stream.read(chunk_size)
        if chunk:
            buf = buf[pos:] + chunk
            offset += pos
            pos = 0
        while len(buf) - pos >= 2:
            n = int.from_bytes(buf[pos:pos + 2], "big")
            if len(buf) - pos - 2 < n:
                break
            yield buf[pos + 2:pos + 2 + n]
            pos += 2 + n
        if not chunk:
            if pos != len(buf):
                raise ItchError(f"truncated message at file offset {offset + pos}")
            return


def write_frames(stream, messages):
    """Write messages to a binary stream with 2-byte length prefixes."""
    for msg in messages:
        stream.write(len(msg).to_bytes(2, "big"))
        stream.write(msg)


def decode(msg):
    """Decode one message into a dict. Alpha fields keep their padding stripped."""
    if not msg:
        raise ItchError("empty message")
    code = chr(msg[0])
    layout = MESSAGES.get(code)
    if layout is None:
        raise ItchError(f"unknown message type {code!r}")
    if len(msg) != layout.length:
        raise ItchError(f"type {code!r}: length {len(msg)}, spec says {layout.length}")
    out = {}
    for f in layout.fields:
        raw = msg[f.offset:f.offset + f.size]
        if f.kind == ALPHA:
            out[f.name] = raw.decode("ascii").rstrip(" ")
        else:
            out[f.name] = int.from_bytes(raw, "big")
    return out


def encode(fields):
    """Encode a dict of field values into message bytes. Missing fields default to 0 / spaces."""
    code = fields["message_type"]
    layout = MESSAGES[code]
    msg = bytearray(layout.length)
    unknown = set(fields) - {f.name for f in layout.fields}
    if unknown:
        raise ItchError(f"type {code!r}: unknown fields {sorted(unknown)}")
    for f in layout.fields:
        if f.kind == ALPHA:
            text = fields.get(f.name, "").encode("ascii")
            if len(text) > f.size:
                raise ItchError(f"{code}.{f.name}: {text!r} longer than {f.size} bytes")
            raw = text.ljust(f.size, b" ")
        else:
            raw = int(fields.get(f.name, 0)).to_bytes(f.size, "big")
        msg[f.offset:f.offset + f.size] = raw
    return bytes(msg)

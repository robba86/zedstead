"""LZO1X decompression, pure Python.

A DayZ world file (``.wrp``) stores its large arrays LZO1X-compressed, without
their compressed length: a stream ends at its own end marker, and only
decompressing it says where the next one starts. This is the reference
decoder's state machine (``lzo1x_d.ch``), small enough not to need a
dependency. It is not fast, but it runs once per world file and the result is
cached (`app.maps.terrain_map`).

The stream does not store its decompressed length either, and LZO1X turns
three bytes of a run into up to 255 bytes of output, so every caller says how
much its array can hold (``max_out``) and a stream that would write past it is
refused before it does.
"""

from __future__ import annotations


class LzoError(ValueError):
    """The bytes are not an LZO1X stream, or it is cut short."""


class LzoTooLarge(ValueError):
    """The stream decompresses to more than its caller said the array can hold.

    Not an `LzoError`: a stream cut short by a short read decodes further from
    a longer one (`terrain_map.read_prefix` reads more), while one that is too
    large stays too large however much more of it is read.
    """


def decompress(src: bytes, ip: int = 0, *, max_out: int) -> tuple[bytearray, int]:
    """Decompress the LZO1X stream starting at ``src[ip]``.

    Returns the output and the offset just past the stream's end marker.
    Raises `LzoTooLarge` before the output would grow past *max_out* bytes.
    """
    try:
        return _decompress(src, ip, max_out)
    except IndexError as exc:
        raise LzoError("LZO stream ends early or points before its start") from exc


def _room(dst: bytearray, n: int, max_out: int) -> None:
    if len(dst) + n > max_out:
        raise LzoTooLarge(f"the LZO stream decompresses to more than {max_out} bytes")


def _copy_match(dst: bytearray, m: int, n: int, max_out: int) -> None:
    if m < 0 or m >= len(dst):
        raise IndexError("match outside the output")
    _room(dst, n, max_out)
    distance = len(dst) - m
    if distance >= n:
        dst += dst[m:m + n]
    else:
        # The match overlaps what it is writing: the last `distance` bytes,
        # repeated. A byte at a time this ran at about 28 MB/s.
        pattern = bytes(dst[m:])
        whole, part = divmod(n, distance)
        dst += pattern * whole
        dst += pattern[:part]


def _decompress(src: bytes, ip: int, max_out: int) -> tuple[bytearray, int]:
    dst = bytearray()
    t = src[ip]
    if t > 17:
        ip += 1
        t -= 17
        _room(dst, t, max_out)
        dst += src[ip:ip + t]
        ip += t
        if t < 4:
            t = src[ip]
            ip += 1
            state = "match"
        else:
            state = "first_literal_run"
    else:
        state = "loop"

    while True:
        if state == "loop":
            t = src[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                while src[ip] == 0:
                    t += 255
                    ip += 1
                t += 15 + src[ip]
                ip += 1
            n = t + 3
            if ip + n > len(src):
                raise IndexError("literal run past the end")
            _room(dst, n, max_out)
            dst += src[ip:ip + n]
            ip += n
            state = "first_literal_run"
        elif state == "first_literal_run":
            t = src[ip]
            ip += 1
            if t >= 16:
                state = "match"
                continue
            m = len(dst) - (1 + 0x0800) - (t >> 2) - (src[ip] << 2)
            ip += 1
            _copy_match(dst, m, 3, max_out)
            state = "match_done"
        elif state == "match":
            if t >= 64:
                m = len(dst) - 1 - ((t >> 2) & 7) - (src[ip] << 3)
                ip += 1
                n = (t >> 5) + 1
            elif t >= 32:
                t &= 31
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 31 + src[ip]
                    ip += 1
                m = len(dst) - 1 - ((src[ip] >> 2) + (src[ip + 1] << 6))
                ip += 2
                n = t + 2
            elif t >= 16:
                m = len(dst) - ((t & 8) << 11)
                t &= 7
                if t == 0:
                    while src[ip] == 0:
                        t += 255
                        ip += 1
                    t += 7 + src[ip]
                    ip += 1
                m -= (src[ip] >> 2) + (src[ip + 1] << 6)
                ip += 2
                if m == len(dst):
                    return dst, ip
                m -= 0x4000
                n = t + 2
            else:
                m = len(dst) - 1 - (t >> 2) - (src[ip] << 2)
                ip += 1
                n = 2
            _copy_match(dst, m, n, max_out)
            state = "match_done"
        else:  # match_done
            t = src[ip - 2] & 3
            if t == 0:
                state = "loop"
                continue
            _room(dst, t, max_out)
            dst += src[ip:ip + t]
            ip += t
            t = src[ip]
            ip += 1
            state = "match"

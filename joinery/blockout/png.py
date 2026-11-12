"""Read a PNG into RGBA pixels, with no image library: the reader for the user's own
painted level samples (``joinery.blockout.grow``).

Non-interlaced PNGs of every colour type at 8 bits per channel, palettes and grey at
1, 2, 4 or 8 bits, and 16-bit channels (their high byte is kept). That is what paint
programs write; anything else is refused with a reason.
"""

from __future__ import annotations

import struct
import zlib

RGBA = tuple[int, int, int, int]
SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: Larger images are not level samples.
MAX_PIXELS = 4096 * 4096
#: Channels per colour type.
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


class PngError(ValueError):
    pass


def _paeth(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c


def _unfilter(
    data: bytes, width: int, height: int, bpp: int, stride: int
) -> list[bytes]:
    rows: list[bytes] = []
    previous = bytearray(stride)
    at = 0
    for _ in range(height):
        if at >= len(data):
            raise PngError("The PNG's image data ends early.")
        kind = data[at]
        row = bytearray(data[at + 1 : at + 1 + stride])
        at += 1 + stride
        if len(row) != stride:
            raise PngError("The PNG's image data ends early.")
        for i in range(stride):
            left = row[i - bpp] if i >= bpp else 0
            up = previous[i]
            corner = previous[i - bpp] if i >= bpp else 0
            if kind == 1:
                row[i] = (row[i] + left) & 0xFF
            elif kind == 2:
                row[i] = (row[i] + up) & 0xFF
            elif kind == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif kind == 4:
                row[i] = (row[i] + _paeth(left, up, corner)) & 0xFF
            elif kind != 0:
                raise PngError(f"Unknown PNG row filter {kind}.")
        rows.append(bytes(row))
        previous = row
    return rows


def _samples(row: bytes, count: int, depth: int) -> list[int]:
    """``count`` samples of ``depth`` bits from a row (16-bit: the high byte)."""
    if depth == 8:
        return list(row[:count])
    if depth == 16:
        return [row[i * 2] for i in range(count)]
    per = 8 // depth
    mask = (1 << depth) - 1
    out = []
    for i in range(count):
        byte = row[i // per]
        shift = 8 - depth * (i % per + 1)
        out.append((byte >> shift) & mask)
    return out


def read(data: bytes) -> tuple[int, int, list[list[RGBA]]]:
    """(width, height, rows of RGBA pixels) from PNG bytes."""
    if not data.startswith(SIGNATURE):
        raise PngError("Not a PNG file.")
    at = len(SIGNATURE)
    header: tuple[int, ...] | None = None
    palette: list[tuple[int, int, int]] = []
    alpha: list[int] = []
    idat = bytearray()
    while at + 8 <= len(data):
        (length,) = struct.unpack(">I", data[at : at + 4])
        kind = data[at + 4 : at + 8]
        body = data[at + 8 : at + 8 + length]
        at += 12 + length
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = [
                (body[i], body[i + 1], body[i + 2]) for i in range(0, len(body) - 2, 3)
            ]
        elif kind == b"tRNS":
            alpha = list(body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if header is None:
        raise PngError("The PNG has no header.")
    width, height, depth, colour, _, _, interlace = header
    if colour not in _CHANNELS:
        raise PngError(f"Unknown PNG colour type {colour}.")
    if interlace:
        raise PngError(
            "Interlaced PNGs aren't read; save the sample without interlacing."
        )
    if depth not in {1, 2, 4, 8, 16} or (colour in {2, 4, 6} and depth < 8):
        raise PngError(f"PNG bit depth {depth} isn't read for colour type {colour}.")
    if width * height > MAX_PIXELS or not width or not height:
        raise PngError(f"The PNG is {width}x{height}; a level sample is smaller.")
    channels = _CHANNELS[colour]
    bits = depth * channels
    bpp = max(1, bits // 8)
    stride = (width * bits + 7) // 8
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise PngError(f"The PNG's image data is broken: {exc}") from None
    pixels: list[list[RGBA]] = []
    for row in _unfilter(raw, width, height, bpp, stride):
        values = _samples(row, width * channels, depth)
        out: list[RGBA] = []
        for x in range(width):
            v = values[x * channels : (x + 1) * channels]
            if colour == 0:
                grey = v[0] * 255 // ((1 << depth) - 1) if depth < 8 else v[0]
                out.append((grey, grey, grey, 255))
            elif colour == 2:
                out.append((v[0], v[1], v[2], 255))
            elif colour == 3:
                if v[0] >= len(palette):
                    raise PngError("A PNG pixel points past the end of its palette.")
                r, g, b = palette[v[0]]
                out.append((r, g, b, alpha[v[0]] if v[0] < len(alpha) else 255))
            elif colour == 4:
                out.append((v[0], v[0], v[0], v[1]))
            else:
                out.append((v[0], v[1], v[2], v[3]))
        pixels.append(out)
    return width, height, pixels

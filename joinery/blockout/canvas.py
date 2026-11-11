"""A tiny deterministic RGB canvas that writes PNG: filled polygons, rects, circles and
thick lines, nothing else. Joinery draws plans with it (no image library needed), and
the same input always gives the same bytes."""

from __future__ import annotations

import math
import struct
import zlib

RGB = tuple[int, int, int]


class Canvas:
    def __init__(self, width: int, height: int, background: RGB) -> None:
        self.width = max(1, width)
        self.height = max(1, height)
        self.rows = [
            bytearray(bytes(background) * self.width) for _ in range(self.height)
        ]

    def span(self, y: int, x0: int, x1: int, colour: RGB) -> None:
        if not 0 <= y < self.height:
            return
        x0, x1 = max(x0, 0), min(x1, self.width - 1)
        if x1 < x0:
            return
        self.rows[y][x0 * 3 : (x1 + 1) * 3] = bytes(colour) * (x1 - x0 + 1)

    def polygon(self, points: list[tuple[float, float]], colour: RGB) -> None:
        """Fill ``points`` (even-odd rule), sampling at pixel centres."""
        if len(points) < 3:
            return
        ys = [p[1] for p in points]
        top = max(math.floor(min(ys)), 0)
        bottom = min(math.ceil(max(ys)), self.height - 1)
        count = len(points)
        for y in range(top, bottom + 1):
            sample = y + 0.5
            crossings: list[float] = []
            for i in range(count):
                (x1, y1), (x2, y2) = points[i], points[(i + 1) % count]
                if (y1 <= sample) != (y2 <= sample):
                    crossings.append(x1 + (sample - y1) * (x2 - x1) / (y2 - y1))
            crossings.sort()
            for a, b in zip(crossings[::2], crossings[1::2], strict=False):
                self.span(y, math.ceil(a - 0.5), math.floor(b - 0.5), colour)

    def rect(self, x0: float, y0: float, x1: float, y1: float, colour: RGB) -> None:
        self.polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], colour)

    def circle(self, cx: float, cy: float, radius: float, colour: RGB) -> None:
        sides = max(12, int(radius * 2))
        self.polygon(
            [
                (
                    cx + radius * math.cos(math.tau * k / sides),
                    cy + radius * math.sin(math.tau * k / sides),
                )
                for k in range(sides)
            ],
            colour,
        )

    def line(
        self, a: tuple[float, float], b: tuple[float, float], width: float, colour: RGB
    ) -> None:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            self.circle(a[0], a[1], width / 2, colour)
            return
        nx, ny = -dy / length * width / 2, dx / length * width / 2
        self.polygon(
            [
                (a[0] + nx, a[1] + ny),
                (b[0] + nx, b[1] + ny),
                (b[0] - nx, b[1] - ny),
                (a[0] - nx, a[1] - ny),
            ],
            colour,
        )

    def png(self) -> bytes:
        raw = b"".join(b"\x00" + bytes(row) for row in self.rows)

        def chunk(kind: bytes, data: bytes) -> bytes:
            body = kind + data
            return (
                struct.pack(">I", len(data))
                + body
                + struct.pack(">I", zlib.crc32(body))
            )

        header = struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b"")
        )

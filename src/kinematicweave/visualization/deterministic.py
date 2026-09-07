"""Small deterministic SVG, PNG, and PDF renderer."""

from __future__ import annotations

from dataclasses import dataclass
import html
from itertools import pairwise
import math
from pathlib import Path
import struct
from typing import Any, Final
import zlib

from PIL import Image, ImageDraw, ImageFont

from kinematicweave.errors import ValidationError

Color = tuple[int, int, int]

WHITE: Final[Color] = (255, 255, 255)
INK: Final[Color] = (31, 41, 55)
MUTED: Final[Color] = (100, 116, 139)
GRID: Final[Color] = (226, 232, 240)
BLUE: Final[Color] = (37, 99, 235)
GREEN: Final[Color] = (5, 150, 105)
CORAL: Final[Color] = (225, 78, 70)
GOLD: Final[Color] = (202, 138, 4)
PURPLE: Final[Color] = (124, 58, 237)
CYAN: Final[Color] = (8, 145, 178)

_FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01111", "10000", "10000", "10000", "10000", "10000", "01111"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01111", "10000", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "J": ("00111", "00010", "00010", "00010", "10010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "10000", "11110", "00001", "00001", "11110"),
    "6": ("01110", "10000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00001", "01110"),
    ".": ("00000", "00000", "00000", "00000", "00000", "00110", "00110"),
    ",": ("00000", "00000", "00000", "00000", "00110", "00100", "01000"),
    ":": ("00000", "00110", "00110", "00000", "00110", "00110", "00000"),
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "/": ("00001", "00010", "00010", "00100", "01000", "01000", "10000"),
    "(": ("00010", "00100", "01000", "01000", "01000", "00100", "00010"),
    ")": ("01000", "00100", "00010", "00010", "00010", "00100", "01000"),
    "%": ("11001", "11010", "00100", "01000", "10110", "00110", "00000"),
    "+": ("00000", "00100", "00100", "11111", "00100", "00100", "00000"),
    "=": ("00000", "11111", "00000", "11111", "00000", "00000", "00000"),
    "<": ("00001", "00010", "00100", "01000", "00100", "00010", "00001"),
    ">": ("10000", "01000", "00100", "00010", "00100", "01000", "10000"),
    " ": ("00000",) * 7,
}


def _hex(color: Color) -> str:
    return "#" + "".join(f"{value:02x}" for value in color)


@dataclass(frozen=True, slots=True)
class Command:
    """One deterministic drawing command."""

    kind: str
    values: tuple[Any, ...]


class Drawing:
    """Record simple drawing commands and emit matching SVG and PNG files."""

    def __init__(self, width: int = 1600, height: int = 1000) -> None:
        if width <= 0 or height <= 0:
            raise ValidationError("drawing dimensions must be positive")
        self.width = width
        self.height = height
        self.commands: list[Command] = []

    def line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: Color = INK,
        width: int = 2,
    ) -> None:
        self.commands.append(Command("line", (x1, y1, x2, y2, color, width)))

    def polyline(
        self,
        points: list[tuple[float, float]],
        color: Color,
        width: int = 3,
    ) -> None:
        if len(points) >= 2:
            self.commands.append(Command("polyline", (tuple(points), color, width)))

    def rect(
        self,
        x: float,
        y: float,
        width: float,
        height: float,
        fill: Color,
        stroke: Color | None = None,
        stroke_width: int = 1,
    ) -> None:
        self.commands.append(
            Command("rect", (x, y, width, height, fill, stroke, stroke_width))
        )

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        fill: Color,
        stroke: Color | None = None,
    ) -> None:
        self.commands.append(Command("circle", (x, y, radius, fill, stroke)))

    def text(
        self,
        x: float,
        y: float,
        value: str,
        color: Color = INK,
        size: int = 22,
        anchor: str = "start",
    ) -> None:
        normalized = value.upper()
        self.commands.append(Command("text", (x, y, normalized, color, size, anchor)))

    def save_svg(self, path: Path, *, title: str) -> None:
        body = [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" '
                f'height="{self.height}" viewBox="0 0 {self.width} {self.height}">'
            ),
            f"<title>{html.escape(title)}</title>",
            f'<rect width="100%" height="100%" fill="{_hex(WHITE)}"/>',
        ]
        for command in self.commands:
            values = command.values
            if command.kind == "line":
                x1, y1, x2, y2, color, width = values
                body.append(
                    f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" '
                    f'y2="{y2:.2f}" stroke="{_hex(color)}" stroke-width="{width}"/>'
                )
            elif command.kind == "polyline":
                points, color, width = values
                joined = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
                body.append(
                    f'<polyline points="{joined}" fill="none" stroke="{_hex(color)}" '
                    f'stroke-width="{width}" stroke-linejoin="round"/>'
                )
            elif command.kind == "rect":
                x, y, width, height, fill, stroke, stroke_width = values
                stroke_value = "none" if stroke is None else _hex(stroke)
                body.append(
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" '
                    f'height="{height:.2f}" fill="{_hex(fill)}" '
                    f'stroke="{stroke_value}" stroke-width="{stroke_width}"/>'
                )
            elif command.kind == "circle":
                x, y, radius, fill, stroke = values
                stroke_value = "none" if stroke is None else _hex(stroke)
                body.append(
                    f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" '
                    f'fill="{_hex(fill)}" stroke="{stroke_value}"/>'
                )
            elif command.kind == "text":
                x, y, value, color, size, anchor = values
                body.append(
                    f'<text x="{x:.2f}" y="{y:.2f}" fill="{_hex(color)}" '
                    f'font-family="Arial, sans-serif" font-size="{size}" '
                    f'font-weight="600" text-anchor="{anchor}" '
                    f'letter-spacing="0">{html.escape(value)}</text>'
                )
        body.append("</svg>\n")
        path.write_text("\n".join(body), encoding="utf-8", newline="\n")

    def save_png(
        self,
        path: Path,
        *,
        grayscale: bool = False,
        font_path: Path | None = None,
    ) -> None:
        """Write a deterministic RGB PNG, optionally converted to grayscale."""

        def output_color(color: Color) -> Color:
            if not grayscale:
                return color
            luminance = round(0.2126 * color[0] + 0.7152 * color[1] + 0.0722 * color[2])
            return (luminance, luminance, luminance)

        pixels = bytearray(WHITE * (self.width * self.height))

        def set_pixel(x: int, y: int, color: Color) -> None:
            if 0 <= x < self.width and 0 <= y < self.height:
                offset = (y * self.width + x) * 3
                pixels[offset : offset + 3] = bytes(color)

        def disk(cx: int, cy: int, radius: int, color: Color) -> None:
            radius = max(radius, 1)
            for yy in range(cy - radius, cy + radius + 1):
                span = int(max(radius * radius - (yy - cy) ** 2, 0) ** 0.5)
                for xx in range(cx - span, cx + span + 1):
                    set_pixel(xx, yy, color)

        def line(
            x1: float,
            y1: float,
            x2: float,
            y2: float,
            color: Color,
            width: int,
        ) -> None:
            dx = x2 - x1
            dy = y2 - y1
            steps = max(int(abs(dx)), int(abs(dy)), 1)
            for index in range(steps + 1):
                ratio = index / steps
                disk(
                    round(x1 + dx * ratio),
                    round(y1 + dy * ratio),
                    max(width // 2, 1),
                    color,
                )

        for command in self.commands:
            values = command.values
            if command.kind == "line":
                x1, y1, x2, y2, color, width = values
                line(x1, y1, x2, y2, output_color(color), width)
            elif command.kind == "polyline":
                points, color, width = values
                for left, right in pairwise(points):
                    line(
                        float(left[0]),
                        float(left[1]),
                        float(right[0]),
                        float(right[1]),
                        output_color(color),
                        width,
                    )
            elif command.kind == "rect":
                x, y, width, height, fill, stroke, stroke_width = values
                fill = output_color(fill)
                stroke = None if stroke is None else output_color(stroke)
                left, top = round(x), round(y)
                right, bottom = round(x + width), round(y + height)
                for yy in range(max(top, 0), min(bottom, self.height)):
                    for xx in range(max(left, 0), min(right, self.width)):
                        set_pixel(xx, yy, fill)
                if stroke is not None:
                    line(left, top, right, top, stroke, stroke_width)
                    line(right, top, right, bottom, stroke, stroke_width)
                    line(right, bottom, left, bottom, stroke, stroke_width)
                    line(left, bottom, left, top, stroke, stroke_width)
            elif command.kind == "circle":
                x, y, radius, fill, stroke = values
                fill = output_color(fill)
                stroke = None if stroke is None else output_color(stroke)
                disk(round(x), round(y), round(radius), fill)
                if stroke is not None:
                    for index in range(360):
                        angle = math.radians(index)
                        set_pixel(
                            round(x + radius * math.cos(angle)),
                            round(y + radius * math.sin(angle)),
                            stroke,
                        )
            elif command.kind == "text" and font_path is None:
                x, y, value, color, size, anchor = values
                color = output_color(color)
                scale = max(1, round(size / 8))
                char_width = 6 * scale
                total = len(value) * char_width
                start = round(x)
                if anchor == "middle":
                    start -= total // 2
                elif anchor == "end":
                    start -= total
                top = round(y) - 7 * scale
                for char_index, char in enumerate(value):
                    glyph = _FONT.get(char, _FONT[" "])
                    for row_index, row in enumerate(glyph):
                        for column_index, bit in enumerate(row):
                            if bit == "1":
                                left = (
                                    start
                                    + char_index * char_width
                                    + column_index * scale
                                )
                                for yy in range(
                                    top + row_index * scale,
                                    top + (row_index + 1) * scale,
                                ):
                                    for xx in range(left, left + scale):
                                        set_pixel(xx, yy, color)
        if font_path is not None:
            if not font_path.is_file():
                raise ValidationError(f"font file does not exist: {font_path}")
            image = Image.frombytes(
                "RGB",
                (self.width, self.height),
                bytes(pixels),
            )
            image_draw = ImageDraw.Draw(image)
            fonts: dict[int, ImageFont.FreeTypeFont] = {}
            for command in self.commands:
                if command.kind != "text":
                    continue
                x, y, value, color, size, anchor = command.values
                font_size = int(size)
                font = fonts.setdefault(
                    font_size,
                    ImageFont.truetype(str(font_path), font_size),
                )
                image_draw.text(
                    (float(x), float(y)),
                    str(value),
                    fill=output_color(color),
                    font=font,
                    anchor={
                        "start": "ls",
                        "middle": "ms",
                        "end": "rs",
                    }[str(anchor)],
                )
            pixels = bytearray(image.tobytes())

        raw = bytearray()
        stride = self.width * 3
        for y in range(self.height):
            raw.append(0)
            raw.extend(pixels[y * stride : (y + 1) * stride])

        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(
                b"IHDR",
                struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0),
            )
            + chunk(b"IDAT", zlib.compress(bytes(raw), level=9))
            + chunk(b"IEND", b"")
        )
        path.write_bytes(png)

    def save_pdf(self, path: Path) -> None:
        """Write the drawing as a deterministic single-page vector PDF."""

        def color_operator(color: Color, *, stroke: bool) -> str:
            values = " ".join(f"{component / 255.0:.6f}" for component in color)
            return f"{values} {'RG' if stroke else 'rg'}"

        def escaped_text(value: str) -> str:
            return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

        def circle_path(x: float, y: float, radius: float) -> str:
            pdf_y = self.height - y
            control = radius * 0.552284749831
            return (
                f"{x + radius:.2f} {pdf_y:.2f} m "
                f"{x + radius:.2f} {pdf_y + control:.2f} "
                f"{x + control:.2f} {pdf_y + radius:.2f} "
                f"{x:.2f} {pdf_y + radius:.2f} c "
                f"{x - control:.2f} {pdf_y + radius:.2f} "
                f"{x - radius:.2f} {pdf_y + control:.2f} "
                f"{x - radius:.2f} {pdf_y:.2f} c "
                f"{x - radius:.2f} {pdf_y - control:.2f} "
                f"{x - control:.2f} {pdf_y - radius:.2f} "
                f"{x:.2f} {pdf_y - radius:.2f} c "
                f"{x + control:.2f} {pdf_y - radius:.2f} "
                f"{x + radius:.2f} {pdf_y - control:.2f} "
                f"{x + radius:.2f} {pdf_y:.2f} c"
            )

        content = [
            "1 J",
            "1 j",
            color_operator(WHITE, stroke=False),
            f"0 0 {self.width} {self.height} re f",
        ]
        for command in self.commands:
            values = command.values
            if command.kind == "line":
                x1, y1, x2, y2, color, width = values
                content.extend(
                    (
                        color_operator(color, stroke=True),
                        f"{width} w",
                        (
                            f"{x1:.2f} {self.height - y1:.2f} m "
                            f"{x2:.2f} {self.height - y2:.2f} l S"
                        ),
                    )
                )
            elif command.kind == "polyline":
                points, color, width = values
                if len(points) < 2:
                    continue
                first, *remaining = points
                path_parts = [f"{first[0]:.2f} {self.height - first[1]:.2f} m"]
                path_parts.extend(
                    f"{x:.2f} {self.height - y:.2f} l" for x, y in remaining
                )
                content.extend(
                    (
                        color_operator(color, stroke=True),
                        f"{width} w",
                        " ".join(path_parts) + " S",
                    )
                )
            elif command.kind == "rect":
                x, y, width, height, fill, stroke, stroke_width = values
                content.append(color_operator(fill, stroke=False))
                if stroke is not None:
                    content.extend(
                        (
                            color_operator(stroke, stroke=True),
                            f"{stroke_width} w",
                        )
                    )
                operator = "B" if stroke is not None else "f"
                content.append(
                    f"{x:.2f} {self.height - y - height:.2f} "
                    f"{width:.2f} {height:.2f} re {operator}"
                )
            elif command.kind == "circle":
                x, y, radius, fill, stroke = values
                content.append(color_operator(fill, stroke=False))
                if stroke is not None:
                    content.extend(
                        (
                            color_operator(stroke, stroke=True),
                            "1 w",
                        )
                    )
                operator = "B" if stroke is not None else "f"
                content.append(f"{circle_path(x, y, radius)} {operator}")
            elif command.kind == "text":
                x, y, value, color, size, anchor = values
                estimated_width = len(value) * size * 0.58
                text_x = float(x)
                if anchor == "middle":
                    text_x -= estimated_width / 2.0
                elif anchor == "end":
                    text_x -= estimated_width
                content.extend(
                    (
                        "BT",
                        color_operator(color, stroke=False),
                        f"/F1 {size} Tf",
                        f"{text_x:.2f} {self.height - y:.2f} Td",
                        f"({escaped_text(value)}) Tj",
                        "ET",
                    )
                )
        stream = ("\n".join(content) + "\n").encode("ascii")
        objects = (
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {self.width} "
                f"{self.height}] /Resources << /Font << /F1 4 0 R >> >> "
                "/Contents 5 0 R >>"
            ).encode("ascii"),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"endstream",
        )
        output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for index, body in enumerate(objects, start=1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n".encode("ascii"))
            output.extend(body)
            output.extend(b"\nendobj\n")
        xref_offset = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
        output.extend(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.extend(
            (
                f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                f"startxref\n{xref_offset}\n%%EOF\n"
            ).encode("ascii")
        )
        path.write_bytes(output)


__all__ = [
    "BLUE",
    "CORAL",
    "CYAN",
    "GOLD",
    "GREEN",
    "GRID",
    "INK",
    "MUTED",
    "PURPLE",
    "WHITE",
    "Drawing",
]

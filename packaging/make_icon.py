"""Render the Nexus Remote icon to PNG sizes and a multi-resolution ICO.

Run from the repository root with the virtual environment's Python:

    .\\packaging\\make_icon.py

The SVG is the source of truth; this script only rasterises it. Qt's QSvg
renderer is already a dependency of PySide6, so no extra packages are needed.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "icon.svg"
OUT_DIR = ROOT.parent / "src" / "nexus_remote" / "resources"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int, renderer: QSvgRenderer) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


def write_ico(entries: list[tuple[int, bytes]], destination: Path) -> None:
    """Assemble a PNG-compressed ICO (Windows Vista and later)."""
    header = struct.pack("<HHH", 0, 1, len(entries))
    directory = b""
    offset = 6 + 16 * len(entries)
    payload = b""
    for size, blob in entries:
        width = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII",
            width,
            width,
            0,
            0,
            1,
            32,
            len(blob),
            offset,
        )
        offset += len(blob)
        payload += blob
    destination.write_bytes(header + directory + payload)


def main() -> int:
    if not SOURCE.exists():
        print(f"missing icon source: {SOURCE}", file=sys.stderr)
        return 1
    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"could not parse {SOURCE}", file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        entries.append((size, png_bytes(render(size, renderer))))
    write_ico(entries, OUT_DIR / "nexus.ico")
    render(512, renderer).save(str(OUT_DIR / "nexus.png"), "PNG")

    print(f"wrote {OUT_DIR / 'nexus.ico'} ({len(ICON_SIZES)} sizes)")
    print(f"wrote {OUT_DIR / 'nexus.png'}")
    del application
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

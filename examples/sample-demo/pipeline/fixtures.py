"""Reproduce the sample's geometric test images; no external captures or product data.

Labels are an embedded bitmap. FreeType hinting differs between Pillow wheels, so a
TrueType draw cannot reproduce the same pixels on every machine that runs the tests.
"""
from pathlib import Path
import argparse
import json

from PIL import Image, ImageDraw

# 5x7 glyphs, top row first. A column of padding is added when drawing.
_GLYPHS = {
    " ": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b00000),
    "A": (0b01110, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "B": (0b11110, 0b10001, 0b10001, 0b11110, 0b10001, 0b10001, 0b11110),
    "C": (0b01111, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b01111),
    "D": (0b11110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b11110),
    "E": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b11111),
    "F": (0b11111, 0b10000, 0b10000, 0b11110, 0b10000, 0b10000, 0b10000),
    "G": (0b01111, 0b10000, 0b10000, 0b10011, 0b10001, 0b10001, 0b01111),
    "H": (0b10001, 0b10001, 0b10001, 0b11111, 0b10001, 0b10001, 0b10001),
    "I": (0b01110, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "J": (0b00111, 0b00010, 0b00010, 0b00010, 0b00010, 0b10010, 0b01100),
    "K": (0b10001, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010, 0b10001),
    "L": (0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b10000, 0b11111),
    "M": (0b10001, 0b11011, 0b10101, 0b10101, 0b10001, 0b10001, 0b10001),
    "N": (0b10001, 0b11001, 0b10101, 0b10011, 0b10001, 0b10001, 0b10001),
    "O": (0b01110, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "P": (0b11110, 0b10001, 0b10001, 0b11110, 0b10000, 0b10000, 0b10000),
    "Q": (0b01110, 0b10001, 0b10001, 0b10001, 0b10101, 0b10010, 0b01101),
    "R": (0b11110, 0b10001, 0b10001, 0b11110, 0b10100, 0b10010, 0b10001),
    "S": (0b01111, 0b10000, 0b10000, 0b01110, 0b00001, 0b00001, 0b11110),
    "T": (0b11111, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100),
    "U": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01110),
    "V": (0b10001, 0b10001, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "W": (0b10001, 0b10001, 0b10001, 0b10101, 0b10101, 0b10101, 0b01010),
    "X": (0b10001, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001, 0b10001),
    "Y": (0b10001, 0b10001, 0b01010, 0b00100, 0b00100, 0b00100, 0b00100),
    "Z": (0b11111, 0b00001, 0b00010, 0b00100, 0b01000, 0b10000, 0b11111),
    "a": (0b00000, 0b00000, 0b01110, 0b00001, 0b01111, 0b10001, 0b01111),
    "b": (0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b11110),
    "c": (0b00000, 0b00000, 0b01110, 0b10000, 0b10000, 0b10000, 0b01110),
    "d": (0b00001, 0b00001, 0b01111, 0b10001, 0b10001, 0b10001, 0b01111),
    "e": (0b00000, 0b00000, 0b01110, 0b10001, 0b11111, 0b10000, 0b01110),
    "f": (0b00110, 0b01001, 0b01000, 0b11100, 0b01000, 0b01000, 0b01000),
    "g": (0b00000, 0b00000, 0b01111, 0b10001, 0b01111, 0b00001, 0b01110),
    "h": (0b10000, 0b10000, 0b11110, 0b10001, 0b10001, 0b10001, 0b10001),
    "i": (0b00100, 0b00000, 0b01100, 0b00100, 0b00100, 0b00100, 0b01110),
    "j": (0b00010, 0b00000, 0b00110, 0b00010, 0b00010, 0b10010, 0b01100),
    "k": (0b10000, 0b10000, 0b10010, 0b10100, 0b11000, 0b10100, 0b10010),
    "l": (0b01100, 0b00100, 0b00100, 0b00100, 0b00100, 0b00100, 0b01110),
    "m": (0b00000, 0b00000, 0b11010, 0b10101, 0b10101, 0b10001, 0b10001),
    "n": (0b00000, 0b00000, 0b10110, 0b11001, 0b10001, 0b10001, 0b10001),
    "o": (0b00000, 0b00000, 0b01110, 0b10001, 0b10001, 0b10001, 0b01110),
    "p": (0b00000, 0b00000, 0b11110, 0b10001, 0b11110, 0b10000, 0b10000),
    "q": (0b00000, 0b00000, 0b01111, 0b10001, 0b01111, 0b00001, 0b00001),
    "r": (0b00000, 0b00000, 0b10110, 0b11001, 0b10000, 0b10000, 0b10000),
    "s": (0b00000, 0b00000, 0b01111, 0b10000, 0b01110, 0b00001, 0b11110),
    "t": (0b01000, 0b01000, 0b11100, 0b01000, 0b01000, 0b01001, 0b00110),
    "u": (0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b10011, 0b01101),
    "v": (0b00000, 0b00000, 0b10001, 0b10001, 0b10001, 0b01010, 0b00100),
    "w": (0b00000, 0b00000, 0b10001, 0b10001, 0b10101, 0b10101, 0b01010),
    "x": (0b00000, 0b00000, 0b10001, 0b01010, 0b00100, 0b01010, 0b10001),
    "y": (0b00000, 0b00000, 0b10001, 0b10001, 0b01111, 0b00001, 0b01110),
    "z": (0b00000, 0b00000, 0b11111, 0b00010, 0b00100, 0b01000, 0b11111),
    ":": (0b00000, 0b00100, 0b00100, 0b00000, 0b00100, 0b00100, 0b00000),
    ".": (0b00000, 0b00000, 0b00000, 0b00000, 0b00000, 0b01100, 0b01100),
}


def _label(image: Image.Image, xy: tuple[int, int], text: str, scale: int, fill: str) -> None:
    """Draw text with the embedded bitmap so the result is identical on every platform."""
    x, y = xy
    width, height = image.size
    for char in text:
        rows = _GLYPHS[char]
        for row, bits in enumerate(rows):
            for col in range(5):
                if bits & (1 << (4 - col)):
                    left = x + col * scale
                    top = y + row * scale
                    if 0 <= left and left + scale <= width and 0 <= top and top + scale <= height:
                        image.paste(fill, (left, top, left + scale, top + scale))
        x += 6 * scale


def generate(root: Path) -> None:
    brand = root / "assets/brand"
    ui = root / "sources/ui"
    brand.mkdir(parents=True, exist_ok=True)
    ui.mkdir(parents=True, exist_ok=True)
    icon = Image.new("RGBA", (512, 512), "#0B5CAD")
    draw = ImageDraw.Draw(icon)
    for x, y in ((96, 96), (224, 224), (352, 352)):
        draw.rectangle((x - 48, y - 48, x + 48, y + 48), fill="#FFFFFF")
    icon.save(brand / "shape-mark.png")

    image = Image.new("RGB", (1920, 1080), "#FFFFFF")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1920, 90), fill="#1A1F26")
    _label(image, (48, 28), "SHAPE BOARD", 5, "#FFFFFF")
    _label(image, (1430, 32), "SYNTHETIC FIXTURE", 4, "#FFFFFF")
    draw.rectangle((0, 90, 420, 1080), fill="#EEF1F4")
    _label(image, (40, 130), "Card group", 4, "#1A1F26")
    for i, label in enumerate(("Card A", "Card B", "Card C")):
        y = 210 + i * 120
        draw.rectangle((28, y - 12, 392, y + 70), outline="#0B5CAD" if i == 1 else "#5A6572", width=3)
        _label(image, (55, y), label, 4, "#1A1F26")
        x = 660 + i * 390
        draw.rectangle((x - 120, 250, x + 120, 510), outline="#0B5CAD", width=4)
        _label(image, (x - 70, 525), label, 4, "#1A1F26")
        if i == 0:
            draw.rectangle((x - 55, 315, x + 55, 425), fill="#0B5CAD")
        elif i == 1:
            draw.ellipse((x - 55, 315, x + 55, 425), fill="#0B5CAD")
            draw.rectangle((x - 132, 238, x + 132, 522), outline="#1A1F26", width=5)
        else:
            draw.polygon(((x, 310), (x - 65, 430), (x + 65, 430)), fill="#0B5CAD")
    _label(image, (490, 150), "Select one card", 5, "#1A1F26")
    draw.rounded_rectangle((1200, 650, 1880, 1010), radius=20, outline="#0B5CAD", width=3)
    _label(image, (1240, 690), "Detail panel", 4, "#1A1F26")
    _label(image, (1240, 770), "Selected: Card B", 4, "#1A1F26")
    _label(image, (1240, 840), "Shape: circle", 4, "#5A6572")
    _label(image, (490, 720), "Selection marker", 4, "#1A1F26")
    _label(image, (490, 800), "Example labels only.", 4, "#5A6572")
    image.save(ui / "board.png")
    (ui / "board-regions.json").write_text(json.dumps({
        "source": "sources/ui/board.png", "frame": [1920, 1080],
        "note": "Boxes from the synthetic image generator.",
        "regions": {"detail_panel": [1200, 650, 680, 360], "selection_marker": [918, 238, 264, 284],
                    "card_list": [28, 198, 364, 322]},
    }, indent=1) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    generate(parser.parse_args().root)

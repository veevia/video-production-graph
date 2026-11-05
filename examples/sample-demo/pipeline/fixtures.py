"""Reproduce the sample's geometric test images; no external captures or product data."""
from pathlib import Path
import argparse
import json

from PIL import Image, ImageDraw, ImageFont


def generate(root: Path) -> None:
    font_path = root / "assets/fonts/MonaSansVF.ttf"

    def font(size):
        return ImageFont.truetype(str(font_path), size)

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
    draw.text((48, 24), "SHAPE BOARD", font=font(40), fill="#FFFFFF")
    draw.text((1430, 28), "SYNTHETIC FIXTURE", font=font(30), fill="#FFFFFF")
    draw.rectangle((0, 90, 420, 1080), fill="#EEF1F4")
    draw.text((40, 130), "Card group", font=font(36), fill="#1A1F26")
    for i, label in enumerate(("Card A", "Card B", "Card C")):
        y = 210 + i * 120
        draw.rectangle((28, y - 12, 392, y + 70), outline="#0B5CAD" if i == 1 else "#5A6572", width=3)
        draw.text((55, y), label, font=font(34), fill="#1A1F26")
        x = 660 + i * 390
        draw.rectangle((x - 120, 250, x + 120, 510), outline="#0B5CAD", width=4)
        draw.text((x - 70, 525), label, font=font(32), fill="#1A1F26")
        if i == 0:
            draw.rectangle((x - 55, 315, x + 55, 425), fill="#0B5CAD")
        elif i == 1:
            draw.ellipse((x - 55, 315, x + 55, 425), fill="#0B5CAD")
            draw.rectangle((x - 132, 238, x + 132, 522), outline="#1A1F26", width=5)
        else:
            draw.polygon(((x, 310), (x - 65, 430), (x + 65, 430)), fill="#0B5CAD")
    draw.text((490, 150), "Select one card", font=font(44), fill="#1A1F26")
    draw.rounded_rectangle((1200, 650, 1880, 1010), radius=20, outline="#0B5CAD", width=3)
    draw.text((1240, 690), "Detail panel", font=font(38), fill="#1A1F26")
    draw.text((1240, 770), "Selected: Card B", font=font(34), fill="#1A1F26")
    draw.text((1240, 840), "Shape: circle", font=font(32), fill="#5A6572")
    draw.text((490, 720), "Selection marker", font=font(36), fill="#1A1F26")
    draw.text((490, 800), "Example labels only.", font=font(30), fill="#5A6572")
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

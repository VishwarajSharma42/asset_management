"""Draws the desktop icon (a tag with NFC waves) as a PNG.   python make_icon.py out.png"""
import sys

from PIL import Image, ImageDraw

SIZE, SCALE = 256, 4
BLUE, WHITE = (29, 78, 216, 255), (255, 255, 255, 255)


def draw(path: str) -> None:
    s = SIZE * SCALE
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s // 5, fill=BLUE)

    cx, cy = int(s * 0.34), s // 2
    d.ellipse((cx - s // 18, cy - s // 18, cx + s // 18, cy + s // 18), fill=WHITE)
    for r in (0.17, 0.30, 0.43):
        rr = int(s * r)
        d.arc((cx - rr, cy - rr, cx + rr, cy + rr), start=-45, end=45, fill=WHITE, width=s // 24)

    img.resize((SIZE, SIZE), Image.LANCZOS).save(path)


if __name__ == "__main__":
    draw(sys.argv[1])

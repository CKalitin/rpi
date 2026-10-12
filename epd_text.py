#!/usr/bin/env python3
"""Write text to a Waveshare 4.2inch e-Paper (V2, 400x300, black/white).

Usage: python3 epd_text.py "I LOVE MY GIRLFRIEND"
"""
import sys
import textwrap
import types

sys.path.insert(0, "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/lib")

# The driver imports RPi.GPIO but never uses it (epdconfig does the GPIO work),
# and the system RPi.GPIO crashes on this old Pi B+ revision code. Stub it out.
sys.modules.setdefault("RPi", types.ModuleType("RPi"))
sys.modules.setdefault("RPi.GPIO", types.ModuleType("RPi.GPIO"))
sys.modules["RPi"].GPIO = sys.modules["RPi.GPIO"]

from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd4in2_V2

FONT = "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"


def render(text, width, height):
    img = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(img)
    # Largest font size where the wrapped text fits the screen.
    for size in range(120, 10, -4):
        font = ImageFont.truetype(FONT, size)
        chars = max(1, int((width - 20) / (size * 0.65)))
        lines = textwrap.wrap(text, chars) or [""]
        line_h = size + 6
        if len(lines) * line_h <= height - 20 and all(
            draw.textlength(l, font=font) <= width - 20 for l in lines
        ):
            break
    y = (height - len(lines) * line_h) // 2
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((width - w) // 2, y), line, font=font, fill=0)
        y += line_h
    return img


def main():
    text = " ".join(sys.argv[1:]) or "Hello"
    epd = epd4in2_V2.EPD()
    try:
        epd.init()
        epd.Clear()
        epd.display(epd.getbuffer(render(text, epd.width, epd.height)))
    finally:
        epd.sleep()


if __name__ == "__main__":
    main()

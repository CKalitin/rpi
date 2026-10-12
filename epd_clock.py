#!/usr/bin/env python3
"""Show the current time and date on a Waveshare 4.2inch e-Paper (V2, 400x300).

Updates every second with a partial refresh (as fast as the panel allows),
and a full refresh every FULL_REFRESH_SECONDS to clear ghosting. Each refresh
duration is logged to stderr.

Note: Waveshare recommends refreshing no more than about once per 3 minutes
for long-term use; a per-second update is a stress test.

Usage: python3 epd_clock.py            # run until Ctrl-C
       python3 epd_clock.py --once     # draw once and exit
       python3 epd_clock.py --minutes  # HH:MM, updated once a minute
"""
import sys
import time
import types
from datetime import datetime

sys.path.insert(0, "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/lib")

# The driver imports RPi.GPIO but never uses it (epdconfig does the GPIO work),
# and the system RPi.GPIO crashes on this old Pi B+ revision code. Stub it out.
sys.modules.setdefault("RPi", types.ModuleType("RPi"))
sys.modules.setdefault("RPi.GPIO", types.ModuleType("RPi.GPIO"))
sys.modules["RPi"].GPIO = sys.modules["RPi.GPIO"]

from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd4in2_V2

FONT = "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"
FULL_REFRESH_SECONDS = 300


_fonts = {}


def get_fonts(draw, width, widest):
    """Load fonts once; pick the largest clock size that fits `widest`."""
    if not _fonts:
        size = 140
        big = ImageFont.truetype(FONT, size)
        while draw.textlength(widest, font=big) > width - 20:
            size -= 4
            big = ImageFont.truetype(FONT, size)
        _fonts["big"] = big
        _fonts["small"] = ImageFont.truetype(FONT, 32)
    return _fonts["big"], _fonts["small"]


def render(now, width, height, seconds):
    img = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(img)
    clock = now.strftime("%H:%M:%S" if seconds else "%H:%M")
    big, small = get_fonts(draw, width, "00:00:00" if seconds else "00:00")

    date = now.strftime("%A %d %B %Y")
    for text, font, y in ((clock, big, 60), (date, small, 205)):
        w = draw.textlength(text, font=font)
        draw.text(((width - w) // 2, y), text, font=font, fill=0)
    return img


def to_buffer(img):
    """Pack a 1-bit image for the panel (MSB first, 1 = white).

    Same result as epd.getbuffer() for a full-size image, but done in C by
    PIL instead of a per-pixel Python loop (about 1.2s faster on a Pi B+).
    """
    return img.tobytes()


def full_refresh(epd, img):
    epd.init_fast(epd.Seconds_1_5S)
    epd.display_Fast(to_buffer(img))


def main():
    once = "--once" in sys.argv
    seconds = "--minutes" not in sys.argv
    interval = 1 if seconds else 60
    epd = epd4in2_V2.EPD()
    try:
        now = datetime.now()
        full_refresh(epd, render(now, epd.width, epd.height, seconds))
        last_full = time.time()
        while not once:
            # Sleep until the next tick, skipping ticks a slow refresh missed.
            time.sleep(interval - time.time() % interval + 0.01)
            now = datetime.now()
            img = render(now, epd.width, epd.height, seconds)
            t0 = time.time()
            if t0 - last_full >= FULL_REFRESH_SECONDS:
                full_refresh(epd, img)
                last_full, kind = time.time(), "full"
            else:
                epd.display_Partial(to_buffer(img))
                kind = "partial"
            print(f"{now:%H:%M:%S} {kind} refresh took {time.time() - t0:.2f}s",
                  file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        epd.sleep()


if __name__ == "__main__":
    main()

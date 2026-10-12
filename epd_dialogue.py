#!/usr/bin/env python3
"""Show the dialogue of a screenplay on a Waveshare 4.2inch e-Paper (V2).

Reads a plain-text screenplay, keeps only spoken lines (dropping action,
scene headings and parentheticals), and shows as many as fit on the screen as:

    CHARACTER what they say, wrapped onto new lines as needed
    CHARACTER 2 ...

Usage: python3 epd_dialogue.py script.txt
       python3 epd_dialogue.py script.txt --print   # print the lines, skip the display
"""
import sys
import types

SPEAKER_INDENT = 20   # character names are centred, indented at least this far
DIALOGUE_INDENT = 10  # dialogue and parentheticals are indented at least this far


def parse_dialogue(text):
    """Return [(character, spoken text), ...] from screenplay-formatted text."""
    entries = []
    current = None
    in_paren = False
    for raw in text.splitlines():
        line = raw.strip()
        indent = len(raw) - len(raw.lstrip())
        if not line:
            current, in_paren = None, False
        elif (indent >= SPEAKER_INDENT and line.isupper()
              and not line.endswith(":") and not line.rstrip(".").isdigit()):
            current = [line, []]
            entries.append(current)
            in_paren = False
        elif current and indent >= DIALOGUE_INDENT:
            if in_paren or line.startswith("("):
                in_paren = ")" not in line
            else:
                current[1].append(line)
        else:
            current, in_paren = None, False
    return [(name, " ".join(words)) for name, words in entries if words]


def wrap(draw, font, entries, width):
    """Word-wrap 'CHARACTER text' entries to `width` pixels; return the lines."""
    lines = []
    for name, spoken in entries:
        words = f"{name} {spoken}".split()
        line = words[0]
        for word in words[1:]:
            if draw.textlength(f"{line} {word}", font=font) <= width:
                line = f"{line} {word}"
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def main():
    path = sys.argv[1]
    entries = parse_dialogue(open(path, encoding="utf-8").read())

    if "--print" in sys.argv:
        for name, spoken in entries:
            print(f"{name} {spoken}")
        return

    sys.path.insert(0, "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/lib")
    # The driver imports RPi.GPIO but never uses it (epdconfig does the GPIO
    # work), and the system RPi.GPIO crashes on this old Pi B+. Stub it out.
    sys.modules.setdefault("RPi", types.ModuleType("RPi"))
    sys.modules.setdefault("RPi.GPIO", types.ModuleType("RPi.GPIO"))
    sys.modules["RPi"].GPIO = sys.modules["RPi.GPIO"]
    from PIL import Image, ImageDraw, ImageFont
    from waveshare_epd import epd4in2_V2

    font = ImageFont.truetype(
        "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc", 17)
    epd = epd4in2_V2.EPD()
    img = Image.new("1", (epd.width, epd.height), 255)
    draw = ImageDraw.Draw(img)

    margin, line_h = 8, 19
    max_lines = (epd.height - 2 * margin) // line_h
    lines = wrap(draw, font, entries, epd.width - 2 * margin)[:max_lines]
    for i, line in enumerate(lines):
        draw.text((margin, margin + i * line_h), line, font=font, fill=0)
    print(f"showing {len(lines)} lines", file=sys.stderr)

    try:
        epd.init_fast(epd.Seconds_1_5S)
        epd.display_Fast(img.tobytes())
    finally:
        epd.sleep()


if __name__ == "__main__":
    main()

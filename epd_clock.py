#!/usr/bin/env python3
"""Show the current time and date on a Waveshare 4.2inch e-Paper (V2, 400x300).

The time comes from the internet (SNTP query to NTP_SERVER, re-synced every
NTP_RESYNC_SECONDS) rather than the Pi's own clock, which is left untouched.
If the query fails, the system clock is used; that is set at boot from the
DS3231 RTC, so it stays right with no network (see README.md).
The shown time is deliberately DISPLAY_OFFSET_SECONDS (+2 min 57 s) ahead.

Updates every second with a partial refresh (as fast as the panel allows),
and a full refresh every FULL_REFRESH_SECONDS to clear ghosting. Each refresh
duration is logged to stderr.

Holding the button (GPIO 26) for 1 s switches between this clock page and the
encoder page (encoder_page.py), which updates once a second whatever the
--minutes setting. The switch redraws at once with a full refresh.

READY_PIN (GPIO 18, header pin 12) drives a status LED. It blinks from startup
until the first frame is drawn, then stays high while refreshes succeed. It
goes low if a refresh fails and when the script exits (including on SIGTERM at
shutdown). Before this script starts, the firmware and the epd-boot-blink
service handle the LED during boot.

Wiring and setup are in /home/admin/rpi/README.md.

Note: Waveshare recommends refreshing no more than about once per 3 minutes
for long-term use; a per-second update is a stress test.

Usage: python3 epd_clock.py            # run until Ctrl-C
       python3 epd_clock.py --once     # draw once and exit
       python3 epd_clock.py --minutes  # HH:MM, updated once a minute
       python3 epd_clock.py --quiet    # don't log each refresh (for unattended runs)
       python3 epd_clock.py --tz=Europe/London   # show another timezone
       python3 epd_clock.py --page=encoder       # start on the encoder page
"""
import signal
import socket
import struct
import sys
import threading
import time
import types
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/lib")

# The driver imports RPi.GPIO but never uses it (epdconfig does the GPIO work),
# and the system RPi.GPIO crashes on this old Pi B+ revision code. Stub it out.
sys.modules.setdefault("RPi", types.ModuleType("RPi"))
sys.modules.setdefault("RPi.GPIO", types.ModuleType("RPi.GPIO"))
sys.modules["RPi"].GPIO = sys.modules["RPi.GPIO"]

from PIL import Image, ImageDraw, ImageFont
from waveshare_epd import epd4in2_V2, epdconfig

import encoder_page

FONT = "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"
FULL_REFRESH_SECONDS = 300
TIMEZONE = "America/Vancouver"  # BC: permanent UTC-7 since 2026-11-01 (tzdata 2026c); not Los_Angeles, which still falls back
NTP_SERVER = "pool.ntp.org"
NTP_RESYNC_SECONDS = 600
BUSY_TIMEOUT = 15  # seconds to wait on the panel before resetting it and retrying
NTP_EPOCH_OFFSET = 2208988800  # seconds between 1900 (NTP) and 1970 (Unix)
READY_PIN = 18  # BCM numbering; "clock running" output (header pin 12)
DISPLAY_OFFSET_SECONDS = 2 * 60 + 57  # added to the shown time (+2 min 57 s)


def ntp_offset(server=NTP_SERVER, timeout=3):
    """Seconds to add to time.time() to get true time, or None if unreachable."""
    packet = b"\x1b" + 47 * b"\0"  # SNTP client request
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            t_send = time.time()
            sock.sendto(packet, (server, 123))
            data, _ = sock.recvfrom(48)
            t_recv = time.time()
    except OSError:
        return None
    secs, frac = struct.unpack("!II", data[40:48])  # server transmit timestamp
    server_time = secs - NTP_EPOCH_OFFSET + frac / 2**32
    return server_time - (t_send + t_recv) / 2  # assume symmetric network delay


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
    quiet = "--quiet" in sys.argv
    seconds = "--minutes" not in sys.argv
    tz = ZoneInfo(next((a[5:] for a in sys.argv if a.startswith("--tz=")), TIMEZONE))
    interval = 1 if seconds else 60

    offset, last_sync = 0.0, 0.0

    def true_time():
        """Internet-corrected Unix time plus DISPLAY_OFFSET_SECONDS; re-syncs
        the NTP offset when it's stale. Ticks are scheduled from this too, so
        they line up with the shown time."""
        nonlocal offset, last_sync
        if time.time() - last_sync >= NTP_RESYNC_SECONDS:
            new = ntp_offset()
            last_sync = time.time()  # on failure, retry after a full interval
            if new is None:
                print(f"NTP sync with {NTP_SERVER} failed; using offset "
                      f"{offset:+.3f}s", file=sys.stderr, flush=True)
            else:
                offset = new
                print(f"synced with {NTP_SERVER}: system clock is "
                      f"{-offset:+.3f}s off", file=sys.stderr, flush=True)
        return time.time() + offset + DISPLAY_OFFSET_SECONDS

    # Exit through the finally block below on SIGTERM (e.g. at shutdown), so the
    # panel is put to sleep and READY_PIN drops instead of being left high.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    epd = epd4in2_V2.EPD()

    # The driver drives GPIO 18 as PWR_PIN, for Waveshare boards with a 9th PWR
    # wire that switches the panel's power. This 8-wire module has no PWR wire,
    # so take the pin over as READY_PIN and make the driver's on/off calls no-ops.
    impl = epdconfig.implementation
    assert impl.PWR_PIN == READY_PIN, "driver's PWR_PIN moved; check READY_PIN"
    ready = impl.GPIO_PWR_PIN  # gpiozero LED, already claimed (low) by the driver
    impl.GPIO_PWR_PIN = types.SimpleNamespace(
        on=lambda: None, off=lambda: None, close=lambda: None)
    # Claiming the pin above ends the boot blinker; keep blinking until the
    # first frame is up. Setting ready.value later stops the blink.
    ready.blink(on_time=0.5, off_time=0.5)

    def read_busy():
        """Like the driver's ReadBusy, but gives up instead of hanging forever."""
        deadline = time.time() + BUSY_TIMEOUT
        while epdconfig.digital_read(epd.busy_pin) == 1:  # 1 = busy
            if time.time() > deadline:
                raise TimeoutError("panel BUSY stuck high")
            epdconfig.delay_ms(20)

    epd.ReadBusy = read_busy

    def show(img, full):
        """Draw img; if the panel hangs, reset it with a full refresh and retry."""
        for attempt in range(1, 6):
            try:
                if full or attempt > 1:
                    full_refresh(epd, img)
                else:
                    epd.display_Partial(to_buffer(img))
                return "full" if full or attempt > 1 else "partial"
            except TimeoutError as e:
                print(f"{datetime.now():%H:%M:%S} {e} (attempt {attempt}/5); "
                      "resetting panel", file=sys.stderr, flush=True)
                time.sleep(3)
        return "failed"

    # A long press (from the button's callback thread) wakes the loop below to
    # switch pages straight away instead of at the next tick.
    page_switch = threading.Event()
    controls = encoder_page.Controls(
        on_hold=page_switch.set,
        log=lambda msg: print(f"{datetime.now():%H:%M:%S} {msg}",
                              file=sys.stderr, flush=True))
    on_clock = "--page=encoder" not in sys.argv

    def frame(now):
        if on_clock:
            return render(now, epd.width, epd.height, seconds)
        return encoder_page.render(controls.snapshot(), epd.width, epd.height)

    try:
        now = datetime.fromtimestamp(true_time(), tz)
        ready.value = show(frame(now), full=True) != "failed"
        last_full = time.time()
        while not once:
            # Sleep until the next tick of the internet-corrected clock, or
            # until a long press. The encoder page always ticks every second.
            tick = interval if on_clock else 1
            switched = page_switch.wait(tick - true_time() % tick + 0.01)
            if switched:
                page_switch.clear()
                on_clock = not on_clock
            now = datetime.fromtimestamp(true_time(), tz)
            img = frame(now)
            t0 = time.time()
            full = switched or t0 - last_full >= FULL_REFRESH_SECONDS
            kind = show(img, full)
            ready.value = kind != "failed"
            if full or kind == "full":
                last_full = time.time()
            if not quiet:
                print(f"{now:%H:%M:%S} {kind} refresh took {time.time() - t0:.2f}s",
                      file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        ready.off()
        controls.close()
        epd.sleep()


if __name__ == "__main__":
    main()

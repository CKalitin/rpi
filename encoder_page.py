"""Rotary encoder + push button input, and the e-Paper page that shows them.

Used by epd_clock.py. Wiring is in README.md ("Rotary encoder and button").

Debouncing is digital, at two levels:
  1. lgpio filters each pin in C: a level change is only reported once the pin
     has held it for BOUNCE seconds (gpiozero's bounce_time maps to
     lgpio.gpio_set_debounce_micros).
  2. The encoder is decoded by gpiozero's quadrature state machine, which only
     counts a step after the full A/B sequence back to rest (both high), so
     leftover contact noise moves it between states without counting.

Layout (400x300): the encoder overlay fills the left third; the right two
thirds show a rolling 5 s logic-analyser trace of the A, B and SW levels, so
you can see which of A and B changes first for each direction.
"""
import collections
import math
import threading
import time

from gpiozero import Button, RotaryEncoder
from PIL import Image, ImageDraw, ImageFont

ENCODER_A_PIN = 21  # BCM; swap with ENCODER_B_PIN if clockwise counts down
ENCODER_B_PIN = 20
BUTTON_PIN = 26  # active high: low at rest, 3.3 V while pressed
ENCODER_BOUNCE = 0.001  # s; short, so fast turns don't lose steps
BUTTON_BOUNCE = 0.02  # s
HOLD_SECONDS = 1.0  # long-press time that switches pages
DETENTS_PER_TURN = 20  # KY-040-style encoders; sets the pointer's step angle
FONT = "/home/admin/e-Paper/RaspberryPi_JetsonNano/python/pic/Font.ttc"
TRACE_SECONDS = 5  # width of the rolling trace window
TRACE_MAX_EDGES = 2000  # history kept; far more than 5 s of fast turning


class TracedEncoder(RotaryEncoder):
    """RotaryEncoder that reports every debounced A/B change to `on_edge`.

    on_edge(a, b, step) gets pin levels (1 = high) and step = +1/-1 when that
    edge completed a click, else 0. Runs in lgpio's callback thread.
    """

    def __init__(self, a, b, on_edge, **kwargs):
        self._on_edge = on_edge
        super().__init__(a, b, **kwargs)

    def _change_state(self, ticks, edge):
        before = self._steps
        super()._change_state(ticks, edge)
        # edge is (A << 1) | B as active values; active = low (pulled up).
        self._on_edge(1 - (edge >> 1), 1 - (edge & 1), self._steps - before)


class Controls:
    """Debounced encoder and button state, updated from lgpio's callback thread.

    `pressed_since_frame` latches any press, so a tap shorter than the 1 s
    refresh still shows on the next frame. `on_hold` is called (from the
    callback thread) when the button has been held for HOLD_SECONDS. `log`
    is called with a message for each button event.
    """

    def __init__(self, on_hold, log=lambda msg: None):
        # (time, a, b, sw, step) per edge, as pin levels; seeded below.
        self.trace = collections.deque(maxlen=TRACE_MAX_EDGES)
        self._levels = [1, 1, 0]  # until read below; callbacks may fire first
        self.encoder = TracedEncoder(ENCODER_A_PIN, ENCODER_B_PIN, self._edge,
                                     bounce_time=ENCODER_BOUNCE, max_steps=0)
        # pull_up=False enables the internal pull-down, so the pin can't float.
        self.button = Button(BUTTON_PIN, pull_up=False, bounce_time=BUTTON_BOUNCE,
                             hold_time=HOLD_SECONDS)
        self._levels = [int(self.encoder.a.pin.state),
                        int(self.encoder.b.pin.state),
                        int(self.button.pin.state)]
        self._record(0)
        self.presses = 0
        self.last_direction = 0  # +1 clockwise, -1 counter-clockwise
        self.pressed_since_frame = False
        self._lock = threading.Lock()
        self.encoder.when_rotated_clockwise = lambda: self._turned(+1)
        self.encoder.when_rotated_counter_clockwise = lambda: self._turned(-1)
        self._log = log
        self._on_hold = on_hold
        self.button.when_pressed = self._pressed
        self.button.when_released = self._released
        self.button.when_held = self._held

    def _record(self, step):
        self.trace.append((time.monotonic(), *self._levels, step))

    def _edge(self, a, b, step):
        self._levels[0:2] = a, b
        self._record(step)

    def _turned(self, direction):
        self.last_direction = direction

    def _pressed(self):
        with self._lock:
            self.presses += 1
            self.pressed_since_frame = True
        self._levels[2] = 1  # active high
        self._record(0)
        self._log(f"button pressed ({self.presses})")

    def _released(self):
        self._levels[2] = 0
        self._record(0)
        self._log("button released")

    def _held(self):
        self._log(f"button held {HOLD_SECONDS:g} s: switching page")
        self._on_hold()

    def raw_levels(self):
        """Debounced pin levels as 0/1, e.g. 'A1 B1 SW0' (A/B idle high)."""
        a = self.encoder.a.pin.state
        b = self.encoder.b.pin.state
        sw = self.button.pin.state
        return f"A{a:d} B{b:d} SW{sw:d}"

    def snapshot(self):
        """State for one frame; clears the press latch."""
        with self._lock:
            pressed = self.button.is_pressed or self.pressed_since_frame
            self.pressed_since_frame = False
            return {"steps": self.encoder.steps, "pressed": pressed,
                    "presses": self.presses, "direction": self.last_direction,
                    "raw": self.raw_levels(), "trace": list(self.trace),
                    "now": time.monotonic()}

    def close(self):
        self.encoder.close()
        self.button.close()


_fonts = {}


def _font(size):
    if size not in _fonts:
        _fonts[size] = ImageFont.truetype(FONT, size)
    return _fonts[size]


def _centered(draw, x, y, text, font, fill=0):
    w = draw.textlength(text, font=font)
    draw.text((x - w / 2, y), text, font=font, fill=fill)


def draw_encoder(draw, box, state):
    """Draw the knob overlay inside box = (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2
    cy = y0 + 112
    r = min(x1 - x0, 200) / 2 - 14  # knob radius
    pressed = state["pressed"]

    _centered(draw, cx, y0 + 10, "ENCODER", _font(18))

    # Detent ticks around the knob; the current one is longer.
    step_angle = 2 * math.pi / DETENTS_PER_TURN
    current = state["steps"] % DETENTS_PER_TURN
    for i in range(DETENTS_PER_TURN):
        a = i * step_angle - math.pi / 2  # 0 steps = pointer straight up
        inner = r + 4
        outer = r + (11 if i == current else 7)
        draw.line((cx + inner * math.cos(a), cy + inner * math.sin(a),
                   cx + outer * math.cos(a), cy + outer * math.sin(a)),
                  fill=0, width=3 if i == current else 1)

    # Knob: outline when released, filled (pushed in) when pressed.
    knob = (cx - r, cy - r, cx + r, cy + r)
    if pressed:
        draw.ellipse(knob, fill=0)
        draw.ellipse((cx - r + 6, cy - r + 6, cx + r - 6, cy + r - 6),
                     outline=255, width=1)
    else:
        draw.ellipse(knob, outline=0, width=3)
    ink = 255 if pressed else 0

    # Pointer from near the centre to the rim, at the current detent.
    a = current * step_angle - math.pi / 2
    draw.line((cx + 0.2 * r * math.cos(a), cy + 0.2 * r * math.sin(a),
               cx + (r - 8) * math.cos(a), cy + (r - 8) * math.sin(a)),
              fill=ink, width=5)
    draw.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=ink)

    # Readout under the knob.
    arrow = {1: "CW", -1: "CCW", 0: ""}[state["direction"]]
    _centered(draw, cx, cy + r + 18, f"{state['steps']:+d}", _font(40))
    _centered(draw, cx, cy + r + 62, arrow, _font(18))
    _centered(draw, cx, y1 - 50, state["raw"], _font(16))
    label = "PRESSED" if pressed else f"presses {state['presses']}"
    if pressed:
        w = draw.textlength(label, font=_font(18))
        draw.rectangle((cx - w / 2 - 6, y1 - 30, cx + w / 2 + 6, y1 - 6), fill=0)
    _centered(draw, cx, y1 - 28, label, _font(18), fill=255 if pressed else 0)


def draw_trace(draw, box, state):
    """Rolling TRACE_SECONDS chart of A, B and SW inside box = (x0, y0, x1, y1).

    Time runs left to right with now at the right edge; each pin's line runs
    up to now and its current level is printed after it. A click's 4 edges
    take a few ms, so a quick click shows as a narrow pulse. Clicks are
    marked +1 (clockwise: A changes before B) or -1 under the axis; clicks too
    close together to label separately are summed into one label.
    """
    x0, y0, x1, y1 = box
    events, now = state["trace"], state["now"]  # events: (t, a, b, sw, step)
    t_start = now - TRACE_SECONDS
    draw.text((x0 + 10, y0 + 10), "A / B TRACE", font=_font(18), fill=0)
    label = f"last {TRACE_SECONDS} s"
    draw.text((x1 - 10 - draw.textlength(label, font=_font(14)), y0 + 13),
              label, font=_font(14), fill=0)

    px0, px1 = x0 + 34, x1 - 24

    def x_of(t):
        return px0 + (max(t, t_start) - t_start) / TRACE_SECONDS * (px1 - px0)

    # Start from the last edge before the window: it sets the opening level.
    first = max((i for i, ev in enumerate(events) if ev[0] <= t_start), default=0)
    shown = events[first:]
    lanes = (("A", 1, y0 + 44), ("B", 2, y0 + 104), ("SW", 3, y0 + 164))
    lane_h = 50
    axis_y = y0 + 222

    for name, field, top in lanes:
        hi, lo = top + 6, top + lane_h - 6
        _centered(draw, x0 + 17, (hi + lo) / 2 - 9, name, _font(16))
        for x in range(px0, px1 + 1, 4):  # dotted low baseline
            draw.point((x, lo), fill=0)
        points = []
        for ev in shown:
            x, y = x_of(ev[0]), hi if ev[field] else lo
            if points:
                points.append((x, points[-1][1]))  # hold the old level
            points.append((x, y))  # then step to the new one
        points.append((px1, points[-1][1]))  # current level, up to now
        draw.line(points, fill=0, width=2)
        level = shown[-1][field]
        draw.text((px1 + 6, (hi if level else lo) - 9), str(level),
                  font=_font(14), fill=0)

    # Time axis with a tick each second, and click markers below it.
    draw.line((px0, axis_y, px1, axis_y), fill=0, width=1)
    for s in range(TRACE_SECONDS + 1):
        x = px1 - s * (px1 - px0) / TRACE_SECONDS
        draw.line((x, axis_y - 3, x, axis_y + 3), fill=0, width=1)
    groups = []  # [x, summed steps], merging clicks closer than 18 px
    for ev in shown:
        if ev[4] and ev[0] > t_start:
            x = x_of(ev[0])
            draw.line((x, axis_y - 6, x, axis_y), fill=0, width=2)
            if groups and x - groups[-1][0] < 18:
                groups[-1][1] += ev[4]
            else:
                groups.append([x, ev[4]])
    for x, steps in groups:
        if steps:
            _centered(draw, x, axis_y + 6, f"{steps:+d}", _font(14))

    draw.text((px0, y1 - 26), f"-{TRACE_SECONDS} s", font=_font(14), fill=0)
    _centered(draw, (px0 + px1) / 2, y1 - 26, "+1 = clockwise", _font(14))
    draw.text((px1 - draw.textlength("now", font=_font(14)), y1 - 26), "now",
              font=_font(14), fill=0)


def render(state, width, height):
    img = Image.new("1", (width, height), 255)
    draw = ImageDraw.Draw(img)
    split = width // 3
    draw_encoder(draw, (0, 0, split, height), state)
    draw.line((split, 10, split, height - 10), fill=0, width=1)
    draw_trace(draw, (split + 1, 0, width, height), state)
    return img

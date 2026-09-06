"""Stateless, brightness-limited scenes for the local panel exerciser.

Every color is logical RGB, with a total channel budget of 255.  Time is in
seconds; callers provide elapsed scene time and control their own send cadence.
No scene performs I/O or keeps state, so dropped scheduling slots do not cause
the animation to fall behind real time.
"""

import colorsys
from dataclasses import dataclass
import hashlib
import math


@dataclass(frozen=True)
class Scene:
    name: str
    duration: float


SCENES = (
    Scene("Rainbow: slow (30 s cycle)", 30.0),
    Scene("Rainbow: medium (8 s cycle)", 24.0),
    Scene("Rainbow: fast (2 s cycle)", 24.0),
    Scene("Primary flashes: 0.5 Hz", 24.0),
    Scene("Primary flashes: 1 Hz", 24.0),
    Scene("Primary flashes: 2.5 Hz", 24.0),
    Scene("Random jumps: 1 Hz", 24.0),
    Scene("Random jumps: 2 Hz", 24.0),
    Scene("Random jumps: 5 Hz", 24.0),
    Scene("Random jumps: 10 Hz", 24.0),
    Scene("White breathing", 24.0),
    Scene("Pastel breathing", 24.0),
    Scene("Panel chase", 24.0),
    Scene("Opposed rainbows", 24.0),
    Scene("Red-blue counterfade", 24.0),
    Scene("Walking bits", 24.0),
    Scene("Byte boundaries", 24.0),
    Scene("Alternating bit patterns", 24.0),
    Scene("Black soak: all zero bits", 12.0),
)

_SCENE_INDEX = {scene.name: index for index, scene in enumerate(SCENES)}
_BLACK = (0, 0, 0)
_PRIMARIES = ((255, 0, 0), (0, 255, 0), (0, 0, 255))
_BOUNDARIES = (0, 1, 2, 3, 7, 8, 15, 16, 31, 32, 63, 64,
               127, 128, 129, 254, 255)
_BIT_PATTERNS = (
    (0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (0x55, 0xAA, 0), (0xAA, 0x55, 0),
    (0, 0x55, 0xAA), (0, 0xAA, 0x55),
    (0x55, 0, 0xAA), (0xAA, 0, 0x55),
    (0x55, 0x55, 0x55), (1, 127, 127),
)


def _budget(rgb, brightness=1.0):
    """Allocate an integer brightness budget without rounding above 255."""
    total = sum(rgb)
    if total <= 0 or brightness <= 0:
        return _BLACK
    budget = min(255, max(0, int(round(255 * brightness))))
    scaled = [value * budget / total for value in rgb]
    result = [int(value) for value in scaled]
    # Largest-remainder rounding preserves the requested total and gives exact
    # primary/white endpoints.  Stable ordering makes ties deterministic.
    missing = budget - sum(result)
    order = sorted(range(3), key=lambda i: scaled[i] - result[i], reverse=True)
    for index in order[:missing]:
        result[index] += 1
    return tuple(result)


def _rainbow(hue, brightness=1.0, saturation=1.0):
    return _budget(colorsys.hsv_to_rgb(hue % 1.0, saturation, 1.0), brightness)


def _step(elapsed, rate):
    # Treat floating-point noise around a packet-grid boundary as that boundary.
    return int(math.floor(elapsed * rate + 1e-9))


def _random_color(seed, cycle, step, rate):
    # Unlike mutable PRNG state, this stays reproducible if a frame is skipped.
    identity = "{}:{}:{}:{}".format(seed, cycle, rate, step).encode("ascii")
    digest = hashlib.sha256(identity).digest()
    return _budget((digest[0] + 1, digest[1] + 1, digest[2] + 1))


def color_at(scene, elapsed, panel_index, panel_count, cycle=0, seed=1):
    """Return a scene's logical RGB tuple with sum <= 255.

    ``panel_index`` is zero based.  ``cycle`` selects new deterministic random
    colors on each playlist pass.  Most scenes are synchronized; chase, opposed
    rainbows, counterfade, and pastel breathing use the panel's position.  The
    caller normally supplies ``0 <= elapsed < scene.duration``; larger elapsed
    values remain valid and simply continue the scene's periodic pattern.
    """
    if not isinstance(scene, Scene) or scene.name not in _SCENE_INDEX:
        raise ValueError("unknown exercise scene")
    if not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError("elapsed time must be finite and nonnegative")
    if (not isinstance(panel_count, int) or isinstance(panel_count, bool)
            or panel_count < 1 or not isinstance(panel_index, int)
            or isinstance(panel_index, bool) or not 0 <= panel_index < panel_count):
        raise ValueError("panel index must be within a positive panel count")
    if (not isinstance(seed, int) or isinstance(seed, bool)
            or not isinstance(cycle, int) or isinstance(cycle, bool)
            or cycle < 0):
        raise ValueError("seed and cycle must be integers; cycle cannot be negative")

    index = _SCENE_INDEX[scene.name]
    phase = panel_index / panel_count
    if index < 3:
        return _rainbow(elapsed / (30.0, 8.0, 2.0)[index])
    if index < 6:
        rate = (0.5, 1.0, 2.5)[index - 3]
        half_step = _step(elapsed, 2 * rate)
        return _PRIMARIES[(half_step // 2) % 3] if half_step % 2 == 0 else _BLACK
    if index < 10:
        rate = (1, 2, 5, 10)[index - 6]
        return _random_color(seed, cycle, _step(elapsed, rate), rate)
    if index == 10:
        brightness = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / 6.0)
        return _budget((1, 1, 1), brightness)
    if index == 11:
        brightness = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed / 8.0)
        return _rainbow(elapsed / 24.0 + phase, brightness, saturation=0.45)
    if index == 12:
        # One lit panel moves every half second, changing hue each lap.
        step = _step(elapsed, 2)
        return (_rainbow(step // panel_count / 6.0)
                if step % panel_count == panel_index else _BLACK)
    if index == 13:
        direction = 1 if panel_index % 2 == 0 else -1
        return _rainbow(direction * elapsed / 8.0 + phase)
    if index == 14:
        red = 0.5 + 0.5 * math.cos(2 * math.pi * (elapsed / 8.0 + phase))
        return _budget((red, 0, 1.0 - red))
    if index == 15:
        step = _step(elapsed, 5) % 24
        rgb = [0, 0, 0]
        rgb[step // 8] = 1 << (step % 8)
        return tuple(rgb)
    if index == 16:
        step = _step(elapsed, 5)
        rgb = [0, 0, 0]
        rgb[(step // len(_BOUNDARIES)) % 3] = _BOUNDARIES[step % len(_BOUNDARIES)]
        return tuple(rgb)
    if index == 17:
        return _BIT_PATTERNS[_step(elapsed, 5) % len(_BIT_PATTERNS)]
    return _BLACK

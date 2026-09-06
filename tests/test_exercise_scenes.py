"""Pure scene tests; no sockets, SSH, hardware, or timing waits."""

import dataclasses
import importlib.util
import math
from pathlib import Path
import random
import sys
import unittest


_PATH = Path(__file__).resolve().parents[1] / "tools" / "exercise_scenes.py"
_SPEC = importlib.util.spec_from_file_location("exercise_scenes", str(_PATH))
scenes = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = scenes
_SPEC.loader.exec_module(scenes)


class SceneTests(unittest.TestCase):
    def color(self, index, elapsed, panel_index=0, panel_count=3, **kwargs):
        return scenes.color_at(scenes.SCENES[index], elapsed, panel_index, panel_count, **kwargs)

    def test_playlist_is_unique_frozen_and_seven_and_a_half_minutes(self):
        self.assertEqual(sum(scene.duration for scene in scenes.SCENES), 450)
        self.assertEqual(len({scene.name for scene in scenes.SCENES}), len(scenes.SCENES))
        self.assertTrue(all(12 <= scene.duration <= 30 for scene in scenes.SCENES))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            scenes.SCENES[0].duration = 1

    def test_every_grid_frame_respects_channel_budget(self):
        # Every 10 Hz frame, multiple playlist cycles and panel arrangements.
        for scene in scenes.SCENES:
            for cycle, count in ((0, 1), (1, 3), (19, 6)):
                for panel in range(count):
                    for tick in range(int(scene.duration * 10)):
                        rgb = scenes.color_at(scene, tick / 10.0, panel, count,
                                              cycle=cycle, seed=728)
                        self.assertIsInstance(rgb, tuple)
                        self.assertEqual(len(rgb), 3)
                        self.assertTrue(all(type(value) is int and 0 <= value <= 255 for value in rgb))
                        self.assertLessEqual(sum(rgb), 255, (scene.name, tick, rgb))

    def test_off_grid_times_and_many_seeds_obey_budget(self):
        rng = random.Random(2812)
        for scene in scenes.SCENES:
            for _ in range(200):
                rgb = scenes.color_at(scene, rng.random() * scene.duration,
                                      rng.randrange(3), 3, cycle=rng.randrange(10000),
                                      seed=rng.randrange(-1000000, 1000000))
                self.assertTrue(all(0 <= value <= 255 for value in rgb))
                self.assertLessEqual(sum(rgb), 255)

    def test_rainbows_reach_exact_primaries_and_keep_constant_total(self):
        for index, period in enumerate((30, 8, 2)):
            self.assertEqual(self.color(index, 0), (255, 0, 0))
            self.assertEqual(self.color(index, period / 3), (0, 255, 0))
            self.assertEqual(self.color(index, 2 * period / 3), (0, 0, 255))
            self.assertEqual(self.color(index, period), (255, 0, 0))
            for tick in range(100):
                self.assertEqual(sum(self.color(index, tick * period / 100)), 255)

    def test_flashes_hold_on_off_and_cycle_all_primaries(self):
        for index, rate in enumerate((0.5, 1, 2.5), 3):
            for flash, primary in enumerate(((255, 0, 0), (0, 255, 0), (0, 0, 255))):
                self.assertEqual(self.color(index, flash / rate), primary)
                self.assertEqual(self.color(index, (flash + 0.49) / rate), primary)
                self.assertEqual(self.color(index, (flash + 0.5) / rate), (0, 0, 0))
                self.assertEqual(self.color(index, (flash + 0.99) / rate), (0, 0, 0))
            # Every on/off interval has at least two 10 Hz transmission slots.
            self.assertGreaterEqual(10 / (rate * 2), 2)

    def test_random_jumps_are_deterministic_and_hold_for_their_step(self):
        for index, rate in enumerate((1, 2, 5, 10), 6):
            seen = []
            for step in range(20):
                expected = self.color(index, step / rate, seed=7, cycle=2)
                self.assertEqual(expected, self.color(index, (step + 0.99) / rate, seed=7, cycle=2))
                self.assertEqual(expected, self.color(index, step / rate, panel_index=2, seed=7, cycle=2))
                seen.append(expected)
            # Out-of-order calls and skipped frames cannot alter the sequence.
            self.assertEqual(seen, [self.color(index, step / rate, seed=7, cycle=2) for step in range(20)])
            self.assertTrue(all(left != right for left, right in zip(seen, seen[1:])))
            self.assertNotEqual(seen, [self.color(index, step / rate, seed=8, cycle=2) for step in range(20)])
            self.assertNotEqual(seen, [self.color(index, step / rate, seed=7, cycle=3) for step in range(20)])

    def test_white_and_pastel_breathing_have_black_and_full_endpoints(self):
        self.assertEqual(self.color(10, 0), (0, 0, 0))
        self.assertEqual(self.color(10, 3), (85, 85, 85))
        self.assertEqual(self.color(10, 6), (0, 0, 0))
        for panel in range(3):
            self.assertEqual(self.color(11, 0, panel), (0, 0, 0))
            self.assertEqual(sum(self.color(11, 4, panel)), 255)
            self.assertTrue(all(channel > 0 for channel in self.color(11, 4, panel)))
        self.assertEqual(len({self.color(11, 4, panel) for panel in range(3)}), 3)

    def test_chase_lights_exactly_one_panel(self):
        for count in (1, 3, 6):
            for step in range(30):
                colors = [self.color(12, step / 2, panel, count) for panel in range(count)]
                self.assertEqual([i for i, rgb in enumerate(colors) if any(rgb)], [step % count])
                self.assertEqual(sum(map(sum, colors)), 255)

    def test_panel_gradients_and_counterfade_are_distinct(self):
        for index in (13, 14):
            self.assertGreater(len({self.color(index, 1, panel) for panel in range(3)}), 1)
            self.assertNotEqual(self.color(index, 1), self.color(index, 2))
        self.assertEqual(self.color(14, 0), (255, 0, 0))
        self.assertEqual(self.color(14, 4), (0, 0, 255))

    def test_walk_covers_each_of_24_bits(self):
        observed = {self.color(15, tick / 5) for tick in range(24)}
        expected = set()
        for channel in range(3):
            for bit in range(8):
                rgb = [0, 0, 0]
                rgb[channel] = 1 << bit
                expected.add(tuple(rgb))
        self.assertEqual(observed, expected)

    def test_boundaries_and_bit_patterns_cover_zero_and_one_bytes(self):
        values = (0, 1, 2, 3, 7, 8, 15, 16, 31, 32, 63, 64, 127, 128, 129, 254, 255)
        for channel in range(3):
            for step, value in enumerate(values):
                expected = [0, 0, 0]
                expected[channel] = value
                self.assertEqual(self.color(16, (channel * len(values) + step) / 5), tuple(expected))
        patterns = {self.color(17, step / 5) for step in range(12)}
        for required in ((0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
                         (0x55, 0xAA, 0), (0xAA, 0x55, 0), (85, 85, 85)):
            self.assertIn(required, patterns)
        self.assertEqual(self.color(18, 0), (0, 0, 0))
        self.assertEqual(self.color(18, 11.9), (0, 0, 0))

    def test_invalid_inputs_fail_before_returning_a_color(self):
        for elapsed in (-1, math.inf, -math.inf, math.nan):
            with self.assertRaises(ValueError):
                self.color(0, elapsed)
        for index, count in ((-1, 3), (3, 3), (0, 0), (0, -1), (1.5, 3), (0, True)):
            with self.assertRaises(ValueError):
                self.color(0, 0, index, count)
        with self.assertRaises(ValueError):
            scenes.color_at(scenes.Scene("missing", 24), 0, 0, 3)
        for kwargs in ({"seed": "bad"}, {"cycle": -1}, {"cycle": 0.5}):
            with self.assertRaises(ValueError):
                self.color(0, 0, **kwargs)


if __name__ == "__main__":
    unittest.main()

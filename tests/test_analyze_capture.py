#!/usr/bin/env python3
"""Synthetic, hardware-free tests for the raw Saleae waveform checker."""
import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import analyze_capture as checker


def write_digital(path, edges, begin=0.0, end=1.0, initial=0, **change):
    header = [b'<SALEAE>', 0, 0, initial, begin, end, len(edges)]
    for name, value in change.items():
        header[{'signature': 0, 'version': 1, 'kind': 2, 'state': 3, 'count': 6}[name]] = value
    path.write_bytes(struct.pack('<8siiIddQ', *header) + struct.pack('<{}d'.format(len(edges)), *edges))


def synthetic(lengths, colors, profile='ws2812b', bank_order=(2, 1, 0)):
    """Generate independent edge geometry; no production waveform helpers."""
    banks = (2, 2, 1, 0, 1, 2)
    edges = [[] for _ in lengths]
    cursor = 0.001
    for color in colors:
        rgb = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
        wire = ([rgb[2], rgb[1], rgb[0]] if profile.endswith('-bgr') else
                [rgb[1], rgb[0], rgb[2]] if profile == 'ws2812b' else rgb)
        bits = [int(bool(octet & (1 << b))) for octet in wire for b in range(7, -1, -1)]
        for bank in bank_order:
            members = [i for i in range(6) if banks[i] == bank and lengths[i]]
            if not members:
                continue
            for i in members:
                for bit in range(lengths[i] * 24):
                    start = cursor + bit * 1.2e-6
                    edges[i].extend((start, start + (700e-9 if bits[bit % 24] else 350e-9)))
            cursor += max(lengths[i] for i in members) * 24 * 1.2e-6 + 2e-6
        cursor += 0.001
    return edges, cursor + 0.001


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.lengths = [3, 2, 1, 1, 2, 1]
        self.colors = ['12AB34', '000000', 'FFFFFF']
        self.profile = 'ws2812b'
        self.edges, self.end = synthetic(self.lengths, self.colors)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, begin=0.0, end=None):
        if end is None:
            end = self.end
        for channel, values in enumerate(self.edges):
            initial = sum(edge < begin for edge in values) & 1
            write_digital(self.root / ('digital_{}.bin'.format(channel)),
                          [edge for edge in values if begin <= edge <= end], begin, end, initial)
        (self.root / 'panel.json').write_text(json.dumps({'pixel_type': self.profile, 'string_lengths': self.lengths}))

    def run_check(self, mode='dld', extra=(), save=True):
        if save:
            self.save()
        args = [str(self.root), '--mode', mode, '--json', str(self.root / 'report.json')]
        if mode == 'dld':
            args += ['--config', str(self.root / 'panel.json')]
            if '--allow-any-color' not in extra:
                args += ['--colors', ','.join(self.colors)]
        args += list(extra)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = checker.main(args)
        return code, json.loads((self.root / 'report.json').read_text())

    def assert_fault(self, kind, extra=()):
        code, report = self.run_check(extra=extra)
        self.assertEqual(code, 1, report)
        self.assertGreater(report['violation_counts'].get(kind, 0), 0, report)
        return report

    def test_valid_profiles_and_unequal_lengths(self):
        for self.profile in checker.ORDERS:
            with self.subTest(profile=self.profile):
                self.edges, self.end = synthetic(self.lengths, self.colors, self.profile)
                code, report = self.run_check(extra=('--bank-order', '--expected-frames', '3', '--sequence', ','.join(self.colors)))
                self.assertEqual(code, 0, report)
                self.assertEqual(report['coverage']['complete_pulses'], sum(self.lengths) * 24 * 3)
                self.assertEqual(report['coverage']['complete_channel_frames'], 18)
                self.assertEqual(report['bank_order']['complete_panel_cycles'], 3)
                self.assertEqual(report['channels'][0]['colors_seen'], dict.fromkeys(self.colors, 1))

    def test_high_and_low_short_long_in_same_capture(self):
        # Keep rises fixed: the longer/shorter highs create opposite low errors.
        self.edges[0][1] -= 100e-9
        self.edges[0][3] += 100e-9
        report = self.assert_fault('high_width')
        self.assertGreaterEqual(report['violation_counts']['high_width'], 2)
        self.assertGreaterEqual(report['violation_counts']['low_width'], 2)

    def test_window_endpoints_are_inclusive(self):
        for i in range(1, len(self.edges[0]), 2):
            self.edges[0][i] += 50e-9 if i % 4 == 1 else -50e-9
        code, report = self.run_check()
        self.assertEqual(code, 0, report)
        self.edges, self.end = synthetic(self.lengths, self.colors)
        self.edges[1] = [edge + 50e-9 for edge in self.edges[1]]
        code, report = self.run_check(extra=('--bank-order',))
        self.assertEqual(code, 0, report)

    def test_period_stretch_even_with_valid_high(self):
        self.edges[0][2:] = [edge + 200e-9 for edge in self.edges[0][2:]]
        self.assert_fault('bit_period')

    def test_long_in_frame_low_is_not_hidden_as_reset(self):
        self.edges[0][20:] = [edge + 400e-6 for edge in self.edges[0][20:]]
        self.assert_fault('frame_bit_count')

    def test_short_reset_and_count_fail(self):
        self.edges[0][20:] = [edge + 70e-6 for edge in self.edges[0][20:]]
        report = self.assert_fault('frame_bit_count')
        self.assertIn('reset_too_short', report['violation_counts'])

    def test_missing_and_duplicate_bit(self):
        self.edges[0][20:22] = []
        self.assert_fault('frame_bit_count')
        self.edges, self.end = synthetic(self.lengths, self.colors)
        t = self.edges[0][20]
        self.edges[0][20:] = [edge + 1.2e-6 for edge in self.edges[0][20:]]
        self.edges[0][20:20] = [t, t + 350e-9]
        self.assert_fault('frame_bit_count')

    def test_valid_timing_wrong_bit_content(self):
        self.edges[0][1] = self.edges[0][0] + (350e-9 if self.edges[0][1] - self.edges[0][0] > 525e-9 else 700e-9)
        self.assert_fault('frame_content')

    def test_color_sequence_mismatch(self):
        self.edges, self.end = synthetic(self.lengths, [self.colors[0], self.colors[2], self.colors[1]])
        self.assert_fault('color_sequence', ('--sequence', ','.join(self.colors)))

    def test_any_color_still_checks_uniform_pixels_and_decodes_rgb(self):
        for self.profile in checker.ORDERS:
            self.edges, self.end = synthetic(self.lengths, self.colors, self.profile)
            code, report = self.run_check(extra=('--allow-any-color', '--bank-order'))
            self.assertEqual(code, 0, report)
            self.assertEqual(report['channels'][0]['colors_seen'], dict.fromkeys(self.colors, 1))
        self.edges[0][49] = self.edges[0][48] + (350e-9 if self.edges[0][49] - self.edges[0][48] > 525e-9 else 700e-9)
        self.assert_fault('frame_content', ('--allow-any-color', '--bank-order'))

    def test_any_color_checks_cross_bank_agreement(self):
        different, ignored = synthetic(self.lengths, ['ABCDEF'] * 3)
        self.edges[3] = different[3]
        self.assert_fault('bank_color_mismatch', ('--allow-any-color', '--bank-order'))

    def test_wrong_bank_order(self):
        self.edges, self.end = synthetic(self.lengths, self.colors, bank_order=(2, 0, 1))
        self.assert_fault('bank_order', ('--bank-order',))

    def test_same_bank_skew(self):
        self.edges[1] = [edge + 100e-9 for edge in self.edges[1]]
        self.assert_fault('bank_pin_start_mismatch', ('--bank-order',))

    def test_same_bank_drift_with_individually_valid_symbols(self):
        self.lengths = [300, 300, 0, 0, 0, 0]
        self.colors = ['000000']
        self.edges, self.end = synthetic(self.lengths, self.colors)
        self.edges[1] = [edge + (i // 2) * 30e-9 for i, edge in enumerate(self.edges[1])]
        report = self.assert_fault('bank_rise_skew', ('--bank-order',))
        self.assertIn('bank_fall_skew', report['violation_counts'])
        self.assertNotIn('bit_period', report['violation_counts'])
        self.assertNotIn('low_width', report['violation_counts'])

    def test_all_zero_disabled_and_expected_empty(self):
        self.lengths = [0] * 6
        self.edges, self.end = synthetic(self.lengths, self.colors)
        code, report = self.run_check(extra=('--bank-order', '--expected-frames', '0'))
        self.assertEqual(code, 0, report)
        self.assertEqual(report['coverage']['complete_pulses'], 0)

    def test_all_64_enable_masks(self):
        self.colors = ['12AB34']
        for mask in range(64):
            with self.subTest(mask=mask):
                self.lengths = [1 if mask & (1 << i) else 0 for i in range(6)]
                self.edges, self.end = synthetic(self.lengths, self.colors)
                code, report = self.run_check(extra=('--bank-order', '--expected-frames', '1'))
                self.assertEqual(code, 0, report)
                self.assertEqual(report['coverage']['complete_pulses'], sum(self.lengths) * 24)

    def test_maximum_and_short_strings_in_one_capture(self):
        self.lengths = [300, 1, 299, 2, 0, 17]
        self.colors = ['00FF00']
        self.edges, self.end = synthetic(self.lengths, self.colors)
        code, report = self.run_check(extra=('--bank-order', '--expected-frames', '1'))
        self.assertEqual(code, 0, report)
        self.assertEqual(report['coverage']['complete_pulses'], sum(self.lengths) * 24)

    def test_bank_overlap(self):
        shift = self.edges[2][0] - self.edges[0][0]
        for channel in (2, 4):
            self.edges[channel] = [edge - shift for edge in self.edges[channel]]
        self.assert_fault('bank_overlap', ('--bank-order',))

    def test_disabled_pin_glitch_and_stuck_high(self):
        self.lengths[0] = 0
        self.assert_fault('disabled_channel_not_low')
        self.edges[0] = [0.0005]
        report = self.assert_fault('disabled_channel_not_low')
        self.assertIn('partial_high_already_too_long', report['violation_counts'])

    def test_missing_entire_frame_requires_expected_count(self):
        self.edges, self.end = synthetic(self.lengths, self.colors[:2])
        self.assert_fault('expected_frame_count', ('--expected-frames', '3'))

    def test_partial_boundary_frames_excluded_observable_pulses_checked(self):
        begin = self.edges[0][9] - 100e-9
        end = self.edges[0][-10] + 100e-9
        self.save(begin, end)
        code, report = self.run_check(save=False)
        self.assertEqual(code, 0, report)
        first = report['channels'][0]
        self.assertEqual(first['complete_frames'], 1)
        self.assertEqual(len(first['partial_boundary_frames']), 2)
        self.assertEqual(len(first['partial_highs']), 2)
        # A complete pulse inside the excluded leading fragment still fails.
        self.edges[0][13] += 100e-9
        self.save(begin, end)
        code, report = self.run_check(save=False)
        self.assertEqual(code, 1, report)
        self.assertIn('high_width', report['violation_counts'])

    def test_initial_partial_high_then_reset_then_complete_frame(self):
        self.lengths = [1, 1, 0, 0, 0, 0]
        self.colors = ['000000', 'FFFFFF']
        self.edges, self.end = synthetic(self.lengths, self.colors)
        self.save(self.edges[0][47] - 100e-9)
        code, report = self.run_check(extra=('--bank-order', '--expected-frames', '1'), save=False)
        self.assertEqual(code, 0, report)
        self.assertEqual(report['channels'][0]['complete_frames'], 1)
        self.assertEqual(report['channels'][0]['colors_seen'], {'FFFFFF': 1})

    def test_bank_groups_cut_by_capture_boundaries(self):
        self.colors *= 3
        self.edges, self.end = synthetic(self.lengths, self.colors)
        # Exercise leading cuts inside highs, data lows, resets, and between
        # bank groups; also cut the last command while GPIO2 is active.
        starts = [self.edges[i][0] - 10e-6 for i in (0, 2, 3)]
        starts += [self.edges[0][i] + 100e-9 for i in (0, 1, 47, 143)]
        for begin in starts:
            with self.subTest(begin=begin):
                self.save(begin, self.edges[0][-10] + 100e-9)
                code, report = self.run_check(extra=('--bank-order',), save=False)
                self.assertEqual(code, 0, report)

    def test_truncated_capture_only_is_insufficient_evidence(self):
        self.save(self.edges[0][10], self.edges[0][30])
        code, report = self.run_check(save=False)
        self.assertEqual(code, 1)
        self.assertIn('no_complete_frames', report['violation_counts'])

    def test_legacy_characterizes_without_dld_windows(self):
        self.edges[0][1] = self.edges[0][0] + 200e-9
        code, report = self.run_check(mode='legacy')
        self.assertEqual(code, 0, report)
        self.assertIsNone(report['pass'])
        self.assertEqual(report['status'], 'characterized')
        self.assertEqual(report['violation_count'], 0)
        self.assertNotIn('zero_high', report['channels'][0]['distributions'])
        self.assertEqual(report['channels'][0]['complete_bursts'], 3)
        self.assertEqual(report['channels'][0]['burst_length_pulses']['histogram'], [[72, 3]])
        self.assertEqual(report['channels'][0]['distributions']['pixel_boundary_low_assuming_24_bits']['count'], 6)

    def test_histogram_coverage_and_percentiles_are_bounds(self):
        code, report = self.run_check()
        self.assertEqual(code, 0, report)
        distribution = report['channels'][0]['distributions']['high']
        self.assertEqual(sum(entry[2] for entry in distribution['histogram']), distribution['count'])
        low, high = distribution['percentile_bin_bounds_ns']['50']
        self.assertLessEqual(low, high)
        self.assertLessEqual(high - low, 5.000001)

    def test_limited_examples_keep_full_failure_counts(self):
        for i in range(1, len(self.edges[0]), 2):
            self.edges[0][i] += 100e-9
        report = self.assert_fault('high_width', ('--max-examples', '2'))
        self.assertEqual(len(report['channels'][0]['violations']['examples']), 2)
        self.assertEqual(report['channels'][0]['violations']['counts']['high_width'], len(self.edges[0]) // 2)

    def test_corrupt_headers_payload_and_timestamps(self):
        cases = [dict(signature=b'NOTSALEE'), dict(version=1), dict(kind=1), dict(state=2),
                 dict(count=999999999), dict(begin=float('nan')), dict(end=-1),
                 dict(edges=[0.002, 0.001]), dict(edges=[0.001, 0.001]),
                 dict(edges=[-1, 0.001]), dict(edges=[0.001, 2]), dict(edges=[0.001, float('inf')])]
        for case in cases:
            with self.subTest(case=case):
                self.save()
                values = dict(case)
                edges = values.pop('edges', [0.001, 0.00100035])
                write_digital(self.root / 'digital_0.bin', edges, **values)
                code, report = self.run_check(save=False)
                self.assertEqual(code, 2, report)
                self.assertEqual(report['status'], 'input_error')
        for payload in (b'', b'<SALEAE>', (self.root / 'digital_1.bin').read_bytes()[:-1],
                        (self.root / 'digital_1.bin').read_bytes() + b'x'):
            self.save()
            (self.root / 'digital_0.bin').write_bytes(payload)
            code, report = self.run_check(save=False)
            self.assertEqual(code, 2, report)

    def test_different_channel_ranges_rejected(self):
        self.save()
        write_digital(self.root / 'digital_0.bin', self.edges[0], end=self.end + 1)
        code, report = self.run_check(save=False)
        self.assertEqual(code, 2, report)

    def test_bad_configuration_is_explicit_input_error(self):
        self.save()
        for config in ({'pixel_type': [], 'string_lengths': self.lengths},
                       {'pixel_type': self.profile, 'string_lengths': [True] * 6}):
            (self.root / 'panel.json').write_text(json.dumps(config))
            code, report = self.run_check(save=False)
            self.assertEqual(code, 2, report)

    def test_chunk_boundary_validation_and_analysis(self):
        original = checker.CHUNK
        try:
            checker.CHUNK = 17
            code, report = self.run_check(extra=('--bank-order',))
            self.assertEqual(code, 0, report)
            self.save()
            self.edges[0][17] = self.edges[0][16]
            write_digital(self.root / 'digital_0.bin', self.edges[0], end=self.end)
            code, report = self.run_check(save=False)
            self.assertEqual(code, 2, report)
        finally:
            checker.CHUNK = original


if __name__ == '__main__':
    unittest.main()

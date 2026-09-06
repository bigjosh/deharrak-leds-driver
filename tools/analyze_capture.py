#!/usr/bin/env python3
"""Offline Saleae Logic 2 version-0 digital export analysis (Python 3 + NumPy).

Format source: https://www.saleae.com/support/logic-software/saving-and-exporting-data/binary-export-format-logic-2
No hardware access. Exit 0: DLD pass or legacy characterization; 1: waveform
violations; 2: malformed/unsupported input. See --help and analyze_capture.md.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import struct
import sys

import numpy as np

CHECKER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
HEADER = struct.Struct('<8siiIddQ')
FORMAT_URL = 'https://www.saleae.com/support/logic-software/saving-and-exporting-data/binary-export-format-logic-2'
CHUNK = 1000000
BANKS = (2, 2, 1, 0, 1, 2)
PINS = ('P8_8', 'P8_10', 'P8_12', 'P8_14', 'P8_16', 'P8_18')
ORDERS = {'ws2812b': (1, 0, 2), 'ws2811-hs': (0, 1, 2),
          'ws2812b-bgr': (2, 1, 0), 'ws2811-hs-bgr': (2, 1, 0)}
# Histograms preserve ALL observations. Percentiles are reported as bin bounds,
# never as exact or sampled quantiles. Fine 5 ns bins cover the symbol range.
HIST_EDGES = np.concatenate((np.arange(0, 2005, 5, dtype=float),
                             np.arange(2100, 10100, 100, dtype=float),
                             np.arange(11000, 101000, 1000, dtype=float),
                             np.geomspace(110000, 1e15, 100), [np.inf]))


class InputError(ValueError):
    pass


class Digital:
    def __init__(self, path):
        self.path = Path(path)
        try:
            size = self.path.stat().st_size
            with self.path.open('rb') as stream:
                raw = stream.read(HEADER.size)
        except OSError as error:
            raise InputError('{}: {}'.format(path, error))
        if len(raw) != HEADER.size:
            raise InputError('{}: truncated header (need 44 bytes)'.format(path))
        signature, version, kind, initial, begin, end, count = HEADER.unpack(raw)
        if signature != b'<SALEAE>':
            raise InputError('{}: invalid Saleae identifier'.format(path))
        if version != 0 or kind != 0:
            raise InputError('{}: unsupported version/type {}/{}; require digital v0'.format(path, version, kind))
        if initial not in (0, 1):
            raise InputError('{}: initial state must be 0 or 1'.format(path))
        if (not math.isfinite(begin) or not math.isfinite(end) or end <= begin
                or not math.isfinite((end - begin) * 1e9)):
            raise InputError('{}: invalid capture time range'.format(path))
        if size != HEADER.size + 8 * count:
            raise InputError('{}: size {} differs from header count {} (expected {} bytes)'.format(
                path, size, count, HEADER.size + 8 * count))
        self.initial, self.begin, self.end, self.count = initial, begin, end, count
        self.transitions = (np.memmap(str(path), dtype='<f8', mode='r', offset=HEADER.size, shape=(count,))
                            if count else np.empty(0, dtype=float))
        previous = None
        for offset in range(0, count, CHUNK):
            block = self.transitions[offset:offset + CHUNK]
            if not np.all(np.isfinite(block)):
                raise InputError('{}: nonfinite transition timestamp'.format(path))
            if block[0] < begin or block[-1] > end:
                raise InputError('{}: transition outside capture range'.format(path))
            if (previous is not None and block[0] <= previous) or np.any(block[1:] <= block[:-1]):
                raise InputError('{}: timestamps must be strictly increasing'.format(path))
            previous = block[-1]
        # Rising and falling edges are views into the file, not copies.
        self.rises = self.transitions[initial::2]
        self.falls = self.transitions[initial + 1::2]
        self.complete_pulses = len(self.falls)
        self.final_state = initial ^ (count & 1)
        self.epsilon_ns = max(0.00001, float(np.spacing(max(abs(begin), abs(end), 1))) * 4e9)

    def close(self):
        if isinstance(self.transitions, np.memmap):
            self.transitions._mmap.close()


class Distribution:
    def __init__(self):
        self.count = 0
        self.minimum = math.inf
        self.maximum = -math.inf
        self.total = 0.0
        self.hist = np.zeros(len(HIST_EDGES) - 1, dtype=np.int64)

    def add(self, ns):
        if len(ns):
            self.count += len(ns)
            self.minimum = min(self.minimum, float(np.min(ns)))
            self.maximum = max(self.maximum, float(np.max(ns)))
            self.total += float(np.sum(ns))
            self.hist += np.histogram(ns, bins=HIST_EDGES)[0]

    def report(self):
        if not self.count:
            return {'count': 0, 'min_ns': None, 'max_ns': None, 'mean_ns': None,
                    'percentile_bin_bounds_ns': {}, 'histogram': []}
        cumulative = np.cumsum(self.hist)
        percentiles = {}
        for quantile in (1, 5, 50, 95, 99, 99.9, 100):
            index = int(np.searchsorted(cumulative, max(1, math.ceil(self.count * quantile / 100))))
            percentiles[str(quantile)] = [float(HIST_EDGES[index]),
                                         float(HIST_EDGES[index + 1]) if math.isfinite(HIST_EDGES[index + 1]) else None]
        return {'count': int(self.count), 'min_ns': self.minimum, 'max_ns': self.maximum,
                'mean_ns': self.total / self.count, 'percentile_bin_bounds_ns': percentiles,
                'histogram': [[float(HIST_EDGES[i]), float(HIST_EDGES[i + 1]) if math.isfinite(HIST_EDGES[i + 1]) else None,
                               int(self.hist[i])] for i in np.flatnonzero(self.hist)]}


class Violations:
    def __init__(self, limit=40):
        self.counts = Counter()
        self.examples = []
        self.limit = limit

    def add(self, kind, count=1, **example):
        if count:
            self.counts[kind] += int(count)
            if len(self.examples) < self.limit:
                self.examples.append(dict(kind=kind, **example))

    def array(self, kind, bad, times, values, **extra):
        count = int(np.count_nonzero(bad))
        if count:
            self.counts[kind] += count
            for index in np.flatnonzero(bad)[:max(0, self.limit - len(self.examples))]:
                self.examples.append(dict(kind=kind, time_s=float(times[index]), observed_ns=float(values[index]), **extra))

    def report(self):
        return {'total': sum(self.counts.values()), 'counts': dict(sorted(self.counts.items())),
                'examples': self.examples, 'examples_limit': self.limit}


def normalize_color(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:0[xX])?[0-9a-fA-F]{6}', value):
        raise InputError('colors must be six hexadecimal RGB digits')
    return value[-6:].upper()


def patterns_for(profile, colors):
    result = {}
    for color in colors:
        octets = [int(color[i:i + 2], 16) for i in (0, 2, 4)]
        wire = bytes(octets[i] for i in ORDERS[profile])
        result[color] = np.unpackbits(np.frombuffer(wire, dtype=np.uint8))
    return result


def parse_config(path):
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise InputError('duplicate configuration field {}'.format(key))
            result[key] = value
        return result
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique_pairs)
    except (OSError, ValueError) as error:
        raise InputError('invalid configuration: {}'.format(error))
    if not isinstance(config, dict) or set(config) != {'pixel_type', 'string_lengths'}:
        raise InputError('configuration must contain only pixel_type and string_lengths')
    if not isinstance(config['pixel_type'], str) or config['pixel_type'] not in ORDERS:
        raise InputError('unsupported pixel profile')
    lengths = config['string_lengths']
    if not isinstance(lengths, list) or len(lengths) != 6 or any(type(n) is not int or not 0 <= n <= 300 for n in lengths):
        raise InputError('string_lengths must be six integers in 0..300')
    return config


def analyze_channel(data, index, channel, args, patterns, length):
    violations = Violations(args.max_examples)
    stats = {name: Distribution() for name in ('high', 'low', 'zero_high', 'one_high',
                                              'zero_data_low', 'one_data_low', 'bit_period', 'reset_low',
                                              'pixel_boundary_low_assuming_24_bits',
                                              'pixel_boundary_period_assuming_24_bits')}
    edges = []
    n = data.complete_pulses
    eps = data.epsilon_ns
    for offset in range(0, n, CHUNK):
        stop = min(n, offset + CHUNK)
        rise = data.rises[offset:stop]
        high = (data.falls[offset:stop] - rise) * 1e9
        bits = high >= 525
        stats['high'].add(high)
        if args.mode == 'dld':
            stats['zero_high'].add(high[~bits])
            stats['one_high'].add(high[bits])
            target = np.where(bits, 700, 350)
            violations.array('high_width', np.abs(high - target) > args.tolerance_ns + eps,
                             rise, high, target_zero_ns=350, target_one_ns=700)
        gap_stop = min(stop, len(data.rises) - 1)
        if gap_stop > offset:
            gap_rise = data.rises[offset:gap_stop]
            low = (data.rises[offset + 1:gap_stop + 1] - data.falls[offset:gap_stop]) * 1e9
            period = (data.rises[offset + 1:gap_stop + 1] - gap_rise) * 1e9
            reset = low >= args.reset_separator_us * 1000 - eps
            bits = bits[:len(low)]
            stats['low'].add(low)
            stats['reset_low'].add(low[reset])
            if args.mode == 'dld':
                stats['zero_data_low'].add(low[(~reset) & (~bits)])
                stats['one_data_low'].add(low[(~reset) & bits])
            stats['bit_period'].add(period[~reset])
            edges.extend((np.flatnonzero(reset) + offset + 1).tolist())
            if args.mode == 'dld':
                target = np.where(bits, 500, 850)
                violations.array('low_width', (~reset) & (np.abs(low - target) > args.tolerance_ns + eps),
                                 data.falls[offset:gap_stop], low, target_after_zero_ns=850, target_after_one_ns=500)
                violations.array('bit_period', (~reset) & (np.abs(period - 1200) > args.tolerance_ns + eps),
                                 gap_rise, period, target_ns=1200)
                violations.array('reset_too_short', reset & (low < args.min_reset_us * 1000 - eps),
                                 data.falls[offset:gap_stop], low, minimum_ns=args.min_reset_us * 1000)
    # A low interval at the beginning can be fully observed when initial state
    # was high, but its starting high pulse is partial. Include that low in
    # legacy statistics without pretending to know its bit's expected value.
    if data.initial and data.count and len(data.rises):
        initial_low = (data.rises[0] - data.transitions[0]) * 1e9
        stats['low'].add(np.array([initial_low]))
        if args.mode == 'dld':
            if initial_low >= args.reset_separator_us * 1000 - eps:
                if initial_low < args.min_reset_us * 1000 - eps:
                    violations.add('reset_too_short', time_s=float(data.transitions[0]), observed_ns=initial_low,
                                   minimum_ns=args.min_reset_us * 1000)
            elif min(abs(initial_low - 500), abs(initial_low - 850)) > args.tolerance_ns + eps:
                violations.add('low_width_unknown_previous_bit', time_s=float(data.transitions[0]),
                               observed_ns=initial_low)
    partial_highs = []
    if data.initial:
        end = float(data.transitions[0]) if data.count else data.end
        partial_highs.append({'boundary': 'start', 'time_s': data.begin, 'observed_lower_bound_ns': (end - data.begin) * 1e9})
    if data.final_state and not (data.initial and not data.count):
        start = float(data.transitions[-1])
        partial_highs.append({'boundary': 'end', 'time_s': start, 'observed_lower_bound_ns': (data.end - start) * 1e9})
    if args.mode == 'dld':
        for partial in partial_highs:
            if partial['observed_lower_bound_ns'] > 700 + args.tolerance_ns + eps:
                violations.add('partial_high_already_too_long', **partial)
        if length == 0 and (data.initial or data.count):
            violations.add('disabled_channel_not_low', transitions=int(data.count), initial_state=data.initial)

    frames = []
    partial_frames = []
    colors_seen = Counter()
    complete_frame_bits = 0
    expected_bits = 24 * length if length is not None else None
    boundaries = [0] + edges + [len(data.rises)]
    sequence_positions = None
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        if stop == start:
            continue
        start_s = float(data.rises[start])
        has_fall = stop <= n
        end_s = float(data.falls[stop - 1]) if has_fall else data.end
        boundary_us = args.min_reset_us if args.mode == 'dld' else args.reset_separator_us
        first_low_start = float(data.transitions[0]) if data.initial and data.count else data.begin
        leading_reset = (start > 0 or (start_s - first_low_start) * 1e6 >= boundary_us - eps / 1000)
        trailing_reset = (stop < len(data.rises) or (has_fall and (data.end - end_s) * 1e6 >= boundary_us - eps / 1000))
        complete = leading_reset and trailing_reset and has_fall
        count = stop - start
        record = {'start_s': start_s, 'last_fall_s': end_s if has_fall else None,
                  'bits': count, 'rgb': None, 'content_valid': None,
                  'observed_rise_index': start, 'observed_complete_pulses': min(stop, n) - start}
        if complete:
            frames.append(record)
            complete_frame_bits += count
        else:
            record.update(missing_start_boundary=not leading_reset,
                          missing_end_boundary=not trailing_reset or not has_fall)
            partial_frames.append(record)
        if leading_reset:
            pixel_ends = np.arange(start + 23, min(stop - 1, n), 24)
            stats['pixel_boundary_low_assuming_24_bits'].add((data.rises[pixel_ends + 1] - data.falls[pixel_ends]) * 1e9)
            stats['pixel_boundary_period_assuming_24_bits'].add((data.rises[pixel_ends + 1] - data.rises[pixel_ends]) * 1e9)
        if args.mode != 'dld' or length == 0:
            continue
        if (complete and count != expected_bits) or count > expected_bits:
            violations.add('frame_bit_count', time_s=start_s, observed_bits=count,
                           expected_bits=expected_bits, boundary_partial=not complete)
            if count > expected_bits:
                # All pulses already received vectorized timing checks. Avoid
                # allocating unbounded color buffers for a corrupt long burst.
                continue
        # Content can still be checked on a known prefix/suffix of a partial
        # boundary frame. An unknown phase is tried only for the two boundary
        # fragments, never to excuse an interior frame's missing/extra bits.
        observed_stop = min(stop, n)
        observed = ((data.falls[start:observed_stop] - data.rises[start:observed_stop]) * 1e9 >= 525).astype(np.uint8)
        phases = [0] if leading_reset else ([(expected_bits - count) % 24] if trailing_reset else range(24))
        matches = []
        if len(observed) >= 24 and args.allow_any_color:
            candidate = observed[:24]
            if np.array_equal(observed, candidate[np.arange(len(observed)) % 24]):
                if leading_reset or trailing_reset:
                    phase = 0 if leading_reset else (expected_bits - count) % 24
                    wire = np.packbits(np.roll(candidate, phase)).tolist()
                    rgb = [0, 0, 0]
                    for wire_index, rgb_index in enumerate(ORDERS[args.profile]):
                        rgb[rgb_index] = wire[wire_index]
                    matches = [''.join('{:02X}'.format(value) for value in rgb)]
                else:
                    matches = [None]  # Uniformity known, pixel bit phase unknown.
        elif len(observed) and not args.allow_any_color:
            for color, pattern in patterns.items():
                if any(np.array_equal(observed, pattern[(np.arange(len(observed)) + phase) % 24]) for phase in phases):
                    matches.append(color)
        checkable = bool(len(observed)) and (not args.allow_any_color or len(observed) >= 24)
        if checkable and not matches:
            violations.add('frame_content', time_s=start_s, observed_bits=len(observed),
                           boundary_partial=not complete, first_bits=''.join(str(int(b)) for b in observed[:48]))
        record['content_valid'] = bool(matches) if checkable else None
        record['rgb'] = matches[0] if len(matches) == 1 else None
        if complete and record['rgb']:
            colors_seen[record['rgb']] += 1
            if args.sequence:
                if sequence_positions is None:
                    sequence_positions = {i for i, color in enumerate(args.sequence) if color == record['rgb']}
                else:
                    sequence_positions = {(i + 1) % len(args.sequence) for i in sequence_positions
                                          if args.sequence[(i + 1) % len(args.sequence)] == record['rgb']}
                if not sequence_positions:
                    violations.add('color_sequence', time_s=start_s, rgb=record['rgb'])
                    sequence_positions = None
    if args.mode == 'dld' and length:
        if not frames:
            violations.add('no_complete_frames')
        if args.expected_frames is not None and len(frames) != args.expected_frames:
            violations.add('expected_frame_count', observed=len(frames), expected=args.expected_frames)
    report = {'string_index': index, 'pin': PINS[index], 'bank': BANKS[index], 'saleae_channel': channel,
              'file': str(data.path), 'initial_state': data.initial, 'final_state': data.final_state,
              'begin_s': data.begin, 'end_s': data.end, 'duration_s': data.end - data.begin,
              'transitions': int(data.count), 'complete_pulses': n,
              'complete_high_coverage_s': stats['high'].total / 1e9,
              'complete_frames': len(frames), 'complete_frame_bits': complete_frame_bits,
              'complete_frame_active_span_s': sum(f['last_fall_s'] - f['start_s'] for f in frames),
              'partial_boundary_frames': partial_frames, 'partial_highs': partial_highs,
              'colors_seen': dict(colors_seen), 'distributions': {k: v.report() for k, v in stats.items()},
              'violations': violations.report(), 'numeric_roundoff_allowance_ns': eps}
    if args.mode == 'legacy':
        report['reset_delimited_segments'] = report['complete_frames']
        report['complete_bursts'] = report['complete_frames']
        report['complete_burst_pulses'] = complete_frame_bits
        report['partial_boundary_bursts'] = report.pop('partial_boundary_frames')
        burst_counts = Counter(frame['bits'] for frame in frames)
        report['burst_length_pulses'] = {'count': len(frames), 'min': min(burst_counts) if burst_counts else None,
                                         'max': max(burst_counts) if burst_counts else None,
                                         'histogram': [[count, burst_counts[count]] for count in sorted(burst_counts)]}
        report['complete_frames'] = 0
        report['complete_frame_bits'] = 0
        report['complete_frame_active_span_s'] = 0
        report['distributions'] = {name: value for name, value in report['distributions'].items()
                                   if name in ('high', 'low', 'bit_period', 'reset_low',
                                               'pixel_boundary_low_assuming_24_bits', 'pixel_boundary_period_assuming_24_bits')}
        report['distributions']['rising_interval_below_reset_separator'] = report['distributions'].pop('bit_period')
        report['distributions']['low_at_least_reset_separator'] = report['distributions'].pop('reset_low')
    return report, frames


def check_banks(channel_results, lengths, args):
    """Match all enabled pins to a longest-string anchor in each bank.

    Complete interior frames must match, share color, and form ordered,
    nonoverlapping bank cycles. Only capture-boundary unmatched events can be
    excluded. Timing windows remain at the per-channel checker level.
    """
    violations = Violations(args.max_examples)
    skew_s = (args.bank_skew_ns + max(item[0]['numeric_roundoff_allowance_ns'] for item in channel_results)) * 1e-9
    active = [bank for bank in (2, 1, 0) if any(lengths[i] and BANKS[i] == bank for i in range(6))]
    events = []
    compared_edges = 0
    for bank in active:
        members = [i for i in range(6) if BANKS[i] == bank and lengths[i]]
        anchor = max(members, key=lambda i: lengths[i])
        anchor_frames = channel_results[anchor][1]
        bank_events = {}
        for frame in anchor_frames:
            event = dict(frame, bank=bank)
            bank_events[frame['start_s']] = event
            events.append(event)
        if len(members) == 1:
            continue

        def candidates_for(index):
            # End-truncated frames with a known start can still be compared.
            # An unknown leading fragment has no assured pixel/edge alignment.
            partial = [f for f in channel_results[index][0]['partial_boundary_frames']
                       if not f['missing_start_boundary']]
            return sorted(channel_results[index][1] + partial, key=lambda f: f['start_s'])

        anchor_candidates = candidates_for(anchor)
        anchor_starts = np.array([frame['start_s'] for frame in anchor_candidates])
        anchor_data = Digital(channel_results[anchor][0]['file'])
        try:
            for member in members:
                if member == anchor:
                    continue
                frames = candidates_for(member)
                starts = np.array([frame['start_s'] for frame in frames])
                for source, target, source_index in ((anchor_candidates, starts, anchor), (frames, anchor_starts, member)):
                    for frame in source:
                        where = int(np.searchsorted(target, frame['start_s']))
                        possible = [j for j in (where - 1, where) if 0 <= j < len(target)]
                        if not possible or min(abs(target[j] - frame['start_s']) for j in possible) > skew_s:
                            violations.add('bank_pin_start_mismatch', time_s=frame['start_s'], bank=bank,
                                           string_index=source_index, other_string_index=member if source_index == anchor else anchor)
                member_data = Digital(channel_results[member][0]['file'])
                try:
                    for frame in frames:
                        if not len(anchor_starts):
                            continue
                        where = int(np.searchsorted(anchor_starts, frame['start_s']))
                        possible = [j for j in (where - 1, where) if 0 <= j < len(anchor_starts)]
                        match = min(possible, key=lambda j: abs(anchor_starts[j] - frame['start_s']))
                        other = anchor_candidates[match]
                        if abs(other['start_s'] - frame['start_s']) > skew_s:
                            continue
                        if frame['rgb'] is not None and other['rgb'] is not None and frame['rgb'] != other['rgb']:
                            violations.add('bank_pin_color_mismatch', time_s=frame['start_s'], bank=bank)
                        event = bank_events.get(other['start_s'])
                        if event and frame['last_fall_s'] is not None:
                            event['last_fall_s'] = max(event['last_fall_s'], frame['last_fall_s'])
                        count = min(frame['observed_complete_pulses'], other['observed_complete_pulses'])
                        a = other['observed_rise_index']
                        b = frame['observed_rise_index']
                        for offset in range(0, count, CHUNK):
                            stop = min(count, offset + CHUNK)
                            for kind, left, right in (('bank_rise_skew', anchor_data.rises, member_data.rises),
                                                      ('bank_fall_skew', anchor_data.falls, member_data.falls)):
                                reference = left[a + offset:a + stop]
                                delta_ns = (right[b + offset:b + stop] - reference) * 1e9
                                violations.array(kind, np.abs(delta_ns) > skew_s * 1e9,
                                                 reference, delta_ns, bank=bank, string_index=member,
                                                 reference_string_index=anchor, limit_ns=args.bank_skew_ns)
                                compared_edges += stop - offset
                finally:
                    member_data.close()
        finally:
            anchor_data.close()
    events.sort(key=lambda frame: frame['start_s'])
    cycles = 0
    for previous, current in zip(events[:-1], events[1:]):
        want = active[(active.index(previous['bank']) + 1) % len(active)]
        if current['bank'] != want:
            violations.add('bank_order', time_s=current['start_s'], observed_bank=current['bank'], expected_bank=want)
        if current['start_s'] < previous['last_fall_s'] - skew_s:
            violations.add('bank_overlap', time_s=current['start_s'], previous_bank=previous['bank'], bank=current['bank'])
        if active.index(current['bank']) > active.index(previous['bank']) and current['rgb'] != previous['rgb']:
            violations.add('bank_color_mismatch', time_s=current['start_s'], bank=current['bank'])
    # Count only cycles with every active bank; a capture can begin/end partway
    # through a command even when individual channel frames are complete.
    for i, event in enumerate(events):
        subset = events[i:i + len(active)]
        if [item['bank'] for item in subset] == active:
            cycles += 1
    return {'enabled_bank_order': active, 'complete_bank_events': len(events),
            'complete_panel_cycles': cycles, 'start_skew_limit_ns': args.bank_skew_ns,
            'edge_skew_limit_ns': args.bank_skew_ns, 'shared_prefix_edge_comparisons': compared_edges,
            'violations': violations.report()}


def analyze(args):
    if args.mode == 'dld' and not args.config:
        raise InputError('--config is required in dld mode')
    config = parse_config(args.config) if args.config else None
    args.profile = config['pixel_type'] if config else None
    colors = [normalize_color(value) for value in args.colors.split(',')] if args.colors else []
    args.sequence = [normalize_color(value) for value in args.sequence.split(',')] if args.sequence else None
    if args.sequence:
        if colors and not set(args.sequence) <= set(colors):
            raise InputError('--sequence contains a color excluded by --colors')
        if not colors:
            colors = list(dict.fromkeys(args.sequence))
    if args.allow_any_color and (args.mode != 'dld' or colors or args.sequence):
        raise InputError('--allow-any-color requires dld mode and cannot be combined with --colors/--sequence')
    if args.mode == 'dld' and not colors and not args.allow_any_color:
        raise InputError('--colors, --sequence, or --allow-any-color is required in dld mode')
    if args.bank_order and args.mode != 'dld':
        raise InputError('--bank-order requires dld mode')
    patterns = patterns_for(config['pixel_type'], colors) if config else {}
    channels = args.channels.split(',')
    if len(channels) != 6 or any(not re.fullmatch(r'\d+', value) for value in channels) or len(set(map(int, channels))) != 6:
        raise InputError('--channels must contain six distinct nonnegative integers in string order')
    results = []
    capture_range = None
    for index, channel in enumerate(channels):
        data = Digital(Path(args.capture_dir) / ('digital_{}.bin'.format(int(channel))))
        try:
            if args.mode == 'dld' and data.epsilon_ns > 1:
                raise InputError('{}: timestamp floating-point resolution is inadequate for nanosecond timing'.format(data.path))
            if capture_range is None:
                capture_range = (data.begin, data.end)
            elif max(abs(data.begin - capture_range[0]), abs(data.end - capture_range[1])) > 1e-9:
                raise InputError('channel time ranges differ; one aligned six-channel export is required')
            results.append(analyze_channel(data, index, int(channel), args, patterns,
                                           config['string_lengths'][index] if config else None))
        finally:
            data.close()
    banks = check_banks(results, config['string_lengths'], args) if args.bank_order else None
    counts = Counter()
    for report, frames in results:
        counts.update(report['violations']['counts'])
    if banks:
        counts.update(banks['violations']['counts'])
    return {'schema_version': 1, 'checker_sha256': CHECKER_SHA256, 'format': 'saleae-digital-v0', 'format_source': FORMAT_URL,
            'mode': args.mode, 'status': ('fail' if counts else 'pass') if args.mode == 'dld' else 'characterized',
            'pass': not bool(counts) if args.mode == 'dld' else None,
            'capture_dir': str(Path(args.capture_dir).resolve()),
            'begin_s': capture_range[0], 'end_s': capture_range[1],
            'duration_s': capture_range[1] - capture_range[0], 'configuration': config,
            'allowed_rgb_colors': colors, 'cyclic_rgb_sequence': args.sequence,
            'allow_any_uniform_rgb': args.allow_any_color,
            'expected_frames_per_enabled_channel': args.expected_frames,
            'limits': {'zero_high_ns': [350 - args.tolerance_ns, 350 + args.tolerance_ns],
                       'one_high_ns': [700 - args.tolerance_ns, 700 + args.tolerance_ns],
                       'zero_low_ns': [850 - args.tolerance_ns, 850 + args.tolerance_ns],
                       'one_low_ns': [500 - args.tolerance_ns, 500 + args.tolerance_ns],
                       'bit_period_ns': [1200 - args.tolerance_ns, 1200 + args.tolerance_ns],
                       'reset_separator_us': args.reset_separator_us, 'minimum_reset_us': args.min_reset_us},
            'coverage': {'complete_pulses': sum(item[0]['complete_pulses'] for item in results),
                         'complete_channel_frames': sum(item[0]['complete_frames'] for item in results),
                         'complete_bursts': sum(item[0].get('complete_bursts', 0) for item in results),
                         'complete_frame_bits': sum(item[0]['complete_frame_bits'] for item in results),
                         'note': 'Capture duration is continuous exported time, not summed pulse time. No acquisition-gap information exists in v0.'},
            'violation_count': sum(counts.values()), 'violation_counts': dict(sorted(counts.items())),
            'channels': [item[0] for item in results], 'bank_order': banks,
            'limitations': ['Digital threshold crossing times only; no analog voltage or LED latch/color observation.',
                            'Percentiles are histogram bin bounds over every observation, not exact quantiles.',
                            'Boundary fragments are excluded from full-frame counts; their observable pulse timing is still checked.',
                            'Without --expected-frames, entirely missing commands cannot be detected from idle time alone.',
                            'The format carries no sample rate, hardware threshold, or acquisition-gap metadata; retain acquisition metadata separately.']}


def parser():
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('capture_dir', help='directory containing digital_<channel>.bin files')
    result.add_argument('--mode', choices=('legacy', 'dld'), required=True)
    result.add_argument('--channels', default='0,1,2,3,4,5', help='six Saleae channels in P8_8,10,12,14,16,18 order')
    result.add_argument('--config', help='existing panel JSON with pixel_type and string_lengths')
    result.add_argument('--colors', help='allowed uniform RGB colors, comma separated')
    result.add_argument('--allow-any-color', action='store_true',
                        help='decode arbitrary colors but still require one repeated 24-bit pixel per frame; use --bank-order for agreement across strings')
    result.add_argument('--sequence', help='cyclic RGB sequence; initial captured phase may be arbitrary')
    result.add_argument('--expected-frames', type=int, help='exact complete frames required on EACH enabled channel')
    result.add_argument('--bank-order', action='store_true', help='check same-bank alignment/content and GPIO2 -> GPIO1 -> GPIO0')
    result.add_argument('--bank-skew-ns', type=float, default=50)
    result.add_argument('--tolerance-ns', type=float, default=50)
    result.add_argument('--reset-separator-us', type=float, default=50,
                        help='segmentation only; short/long malformed frame lengths still fail')
    result.add_argument('--min-reset-us', type=float, default=300)
    result.add_argument('--max-examples', type=int, default=40, help='maximum violation examples per channel/bank report')
    result.add_argument('--json', dest='json_path', help='write structured report here (also on input error)')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if (not all(math.isfinite(value) and value > 0 for value in
                    (args.tolerance_ns, args.bank_skew_ns, args.reset_separator_us, args.min_reset_us))
                or args.tolerance_ns >= 175 or args.reset_separator_us * 1000 <= 1200 + args.tolerance_ns
                or args.min_reset_us < args.reset_separator_us or args.max_examples < 0
                or (args.expected_frames is not None and args.expected_frames < 0)):
            raise InputError('invalid timing/count options (windows must not overlap; reset separator must exceed a bit)')
        report = analyze(args)
        code = 1 if report['pass'] is False else 0
    except (InputError, OSError) as error:
        report = {'schema_version': 1, 'checker_sha256': CHECKER_SHA256, 'mode': args.mode,
                  'status': 'input_error', 'pass': False, 'error': str(error)}
        code = 2
    if args.json_path:
        Path(args.json_path).write_text(json.dumps(report, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    if code == 2:
        print('INPUT ERROR: {}'.format(report['error']), file=sys.stderr)
    else:
        count_field = 'complete_bursts' if args.mode == 'legacy' else 'complete_channel_frames'
        label = 'complete bursts' if args.mode == 'legacy' else 'complete channel frames'
        print('{}: {:.6f}s; {} complete pulses; {} {}; {} violations'.format(
            report['status'].upper(), report['duration_s'], report['coverage']['complete_pulses'],
            report['coverage'][count_field], label, report['violation_count']))
        for channel in report['channels']:
            high = channel['distributions']['high']
            print('  {} / D{}: {} pulses, {} {}, high min/max {} / {} ns, {}'.format(
                channel['pin'], channel['saleae_channel'], channel['complete_pulses'],
                channel.get('complete_bursts', channel['complete_frames']), 'bursts' if args.mode == 'legacy' else 'frames',
                high['min_ns'], high['max_ns'], channel['violations']['counts']))
    return code


if __name__ == '__main__':
    sys.exit(main())

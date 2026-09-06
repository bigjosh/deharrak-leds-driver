# Protected overnight evidence

Generated 2026-09-06T13:14:06.130109+00:00.

Only saved analysis JSON and finalized sender summaries were read. The run statuses below retain operational failures and active-run state separately from waveform results.

| Run | Supervisor state | Reviewed captures | Captured seconds | Complete pulses | Complete channel frames | Waveform violations |
|---|---|---:|---:|---:|---:|---:|
| protected-overnight-001 | fail | 117 | 3491.265945728 | 1,010,677,887 | 140,332 | 0 |
| protected-overnight-002 | stopped | 612 | 18257.095062016 | 4,452,206,525 | 663,562 | 0 |

0 chunk directories had no completed analysis at this snapshot and are excluded from coverage.

Each run’s manually reviewed final supplement is included exactly once and separately itemized in the JSON. Run 001 retains its admission failure. Run 002’s final phase is identified as operator stopped when its saved cancellation and successful sender shutdown support that distinction; its original phase status is preserved in the JSON.

| Run / phase | Phase state | Captured seconds | Pulses | Channel frames | Violations | Finalized sender successes / failures |
|---|---|---:|---:|---:|---:|---:|
| 001 / 000-zero-normal | pass | 1527.962576768 | 445,198,958 | 61,821 | 0 | 12,182 / 0 |
| 001 / 001-one-normal | pass | 1513.103555264 | 440,220,690 | 61,123 | 0 | 12,143 / 0 |
| 001 / 002-cycle-normal | fail | 450.199813696 | 125,258,239 | 17,388 | 0 | 3,934 / 1 |
| 002 / 000-cycle-normal | pass | 1170.937828032 | 338,669,950 | 47,018 | 0 | 10,726 / 0 |
| 002 / 001-random-ws2811 | pass | 1349.055937600 | 390,673,768 | 54,240 | 0 | 10,771 / 0 |
| 002 / 002-zero-ddr | pass | 1375.960999552 | 315,869,114 | 43,862 | 0 | 8,538 / 0 |
| 002 / 003-one-ddr | pass | 1373.634558656 | 315,570,153 | 43,822 | 0 | 8,538 / 0 |
| 002 / 004-random-ethernet | pass | 1334.672270976 | 380,557,282 | 52,845 | 0 | 10,601 / 0 |
| 002 / 005-cycle-cpu | pass | 1202.350368512 | 276,191,212 | 38,352 | 0 | 8,542 / 0 |
| 002 / 006-zero-unequal | pass | 1375.759672768 | 237,428,546 | 57,172 | 0 | 11,121 / 0 |
| 002 / 007-one-gpio0 | pass | 1404.666816000 | 127,855,647 | 17,754 | 0 | 20,292 / 0 |
| 002 / 008-zero-bgr | pass | 1296.940312192 | 375,682,345 | 52,159 | 0 | 10,768 / 0 |
| 002 / 009-one-bgr | pass | 1279.066984768 | 370,596,251 | 51,462 | 0 | 10,788 / 0 |
| 002 / 010-cycle-unequal | pass | 1202.579657152 | 207,497,087 | 49,961 | 0 | 11,123 / 0 |
| 002 / 011-random-full-bgr | pass | 1231.447653440 | 356,283,246 | 49,476 | 0 | 10,732 / 0 |
| 002 / 012-zero-ethernet | pass | 1218.422941376 | 347,521,530 | 48,257 | 0 | 10,631 / 0 |
| 002 / 013-one-ethernet | pass | 1201.433214144 | 342,905,072 | 47,616 | 0 | 10,614 / 0 |
| 002 / 014-zero-final | operator stopped | 240.165846848 | 68,905,322 | 9,566 | 0 | 2,132 / 0 |

| Run | Zero highs ns | One highs ns | Zero data lows ns | One data lows ns | Bit periods ns |
|---|---:|---:|---:|---:|---:|
| protected-overnight-001 | 348.000–360.000 | 696.000–712.000 | 848.000–864.000 | 498.000–514.000 | 1206.000–1214.000 |
| protected-overnight-002 | 348.000–360.000 | 696.000–712.000 | 848.000–864.000 | 498.000–514.000 | 1206.000–1214.000 |

- Coverage is the sum of analyzed export intervals, counted once per capture rather than once per channel. Acquisition/export/analysis gaps are uncaptured.
- Captured seconds include reset and idle time; they are not continuous uninterrupted soak time or total wall time. No capture-to-host clock synchronization is inferred.
- Complete pulses include observable pulses within boundary fragments. Complete channel frames exclude partial frames; channel frames are not panel frames or sender commands.
- Panel-cycle counts require complete observable bank groups and can be lower near capture boundaries, especially with unequal string lengths.
- Active phases contribute completed analysis files but no unfinished sender summary. Saved supervisor snapshots may lag a newly completed analysis; this script never changes them.
- Run 001 supervisor captured_seconds already includes its final unprocessed export; checked_seconds did not. Add manual coverage to primary analysis coverage, never again to that supervisor captured_seconds total.
- Run 002 final export was manually validated after operator cancellation and is absent from supervisor coverage totals. Its supplement is added to primary analyses once. Stale exporting metadata does not imply an incomplete acquisition; retained export integrity evidence is separate.
- Finalized sender successes/failures count commands across captured and uncaptured periods. They must not be added to pulse/frame coverage.
- Run 001 stopped after a pre-publication admission timeout under the former 20 ms policy. Run 002 uses the revised 1 s admission policy; keep those runs separately identified.
- Timing extrema and counts combine observations; violation categories can overlap at one physical anomaly. Zero observed violations is not a guaranteed future failure rate.
- The arbitrary-color checker verifies uniform pixels and channel agreement; without separate schedule correlation it does not establish random-schedule identity.
- Digital edges establish waveform behavior, not analog signal integrity or actual LED decoding/latching. Raw evidence and supervisor files remain unchanged.

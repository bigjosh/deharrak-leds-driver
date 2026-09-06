# Final retained capture review

The final 30.025624192-second capture passes the unchanged waveform, CYCLE sequence, and GPIO2→GPIO1→GPIO0 order checks: **3,585,600 pulses, 498 complete channel frames, exactly 83 per string, zero violations, and no partial frames or partial highs**.

Independent raw decoding confirms that every string carries the exact final 83 successful colors in the sender's deterministic schedule. The last complete color is `FFFFEF`, successful send index 3933 (zero based), cycle index 39. The next attempted `FFFFDF` occurrence produced no additional frame; that color also appears earlier in the repeated cycle.

| Bank | Last falling edge, capture-relative | Observed low through capture end |
|---|---:|---:|
| GPIO2 | 14.949701204 s | 15.075922988 s |
| GPIO1 | 15.042698480 s | 14.982925712 s |
| GPIO0 | 15.103422874 s | 14.922201318 s |

The sender recorded its last successful command finishing at 06:25:07.664371 UTC and the next command failing at 06:25:07.756961 UTC. That failure was reported before publication, stage 1, timeout `-110`, blocked engine 3, status `0x01E70106`. The waveform shows complete preceding frames followed by sustained low outputs. It does not identify the admission failure's cause. No exact conversion between capture-relative edge times and board UTC is assumed.

The original supervisor status and raw export remain unchanged. Separate evidence files are `manual-analysis.json`, `manual-analysis.log`, and `manual-review.json`.

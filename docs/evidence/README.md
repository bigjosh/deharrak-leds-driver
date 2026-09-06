# Validation evidence

This directory is a portable, curated record of the 2026-09-05 reference-board
checks and 2026-09-06 physical bench. It supports the
[historical ABI3 report](../validation.md) and
[protected-build report](../validation-20260906.md). It contains no raw capture
archives, transition binaries, executables, kernel modules, host inventories,
SSH configuration, or large build logs.

## Provenance and interpretation

The [manifest](manifest.json) labels every evidence record as either an **exact
byte copy** or a **derived summary**, gives its SHA-256, and identifies original
local files by repository-relative archive path, size, and SHA-256. Derived
JSON records also embed their sources and extraction rule. They select existing
values or verbatim source lines; no measurement values were recomputed for this
publication. Absolute workstation/runtime paths, process inventories, command
arguments, analyzer identifiers, and unrelated terminal details were omitted.
The original local evidence was left unchanged.

The manifest catalogs original local sources, including material deliberately
excluded from Git. Its `archive_path` values are inventory labels, **not links
available in a GitHub checkout**. The curated JSON records retain field names
and status values from their source versions. In particular, run 001 remains
failed after its admission rejection; run 002 remains operator-stopped and its
last phase retains the original generic `fail` label. A later checkout does
not imply that the historical handback configuration is still active.

## Final protected bench

| Record | Purpose |
|---|---|
| [Final overnight summary](2026-09-06/final-overnight-summary.md) | Exact final generated narrative, per-phase counts, extrema, and accounting cautions |
| [Final accounting](2026-09-06/final-overnight-summary.json) | Both admission policies separately; phase metrics, manual supplements, and original analysis paths/hashes |
| [Operational metrics](2026-09-06/operational-metrics.json) | Exact recorded sender cadence, kernel counters, CPSW drain, and injected-load rates |
| [Handback](2026-09-06/handback-summary.json) | Sixteen historical checks, final mailbox/output configuration, CPU/LED state, and final module/CLI identities |
| [Windows handback](2026-09-06/windows-handback-summary.json) | No owned workers; acquisition completion before STOP and retained export-bookkeeping state |
| [Final supplement](2026-09-06/final-supplement-analysis.json) | Last complete export, including observable boundary fragments, counted once |
| [Final export integrity](2026-09-06/final-export-integrity.json) | Six binary header/length checks and native archive ZIP CRC result |
| [Former schedule](2026-09-06/overnight-001-plan.json) / [revised schedule](2026-09-06/overnight-002-plan.json) | Exact preserved phase plans |
| [Preparation state](2026-09-06/preparation-state.json) | Exact saved original CPU/LED settings and preparation status at the time |

The final run contains **4,452,206,525 complete pulses and 663,562 complete
channel frames over 18,257.095062016 captured seconds**, with zero waveform
violations. Its finalized sender summaries record **155,917 successful sends
and no failed sends**. The earlier admission-policy run is a separate result.

Captured seconds sum export intervals once per capture, not once per channel.
They include low/reset time and exclude acquisition/export/analysis gaps.
Complete pulses may occur within boundary fragments; complete channel frames
exclude those fragments and do not equal six-string sends. The manual final
supplement for each run is included exactly once. Commands during uncaptured
intervals are not additional waveform evidence. The arbitrary-color checker
verifies per-frame uniformity and cross-channel agreement; it does not alone
prove random-schedule identity. Digital edges do not establish analog integrity,
LED decoding, or last-pixel latching.

The operational `irq_off_cycles` interval starts after masking and is sampled
before restoration; it excludes entry/restore overhead and is not a hard bound
on all interrupt-masked time. Artifact hashes plus loaded module `srcversion`
identify the recorded build; they are not a hash of live kernel text.

## Earlier failures and intermediate checks

| Record | Purpose |
|---|---|
| [Legacy captures](2026-09-06/legacy-captures.json) | Characterization of three unqualified LEDscape/starfield captures |
| [Userspace short tests](2026-09-06/userspace-short-tests.json) | Reviewed matrix/smoke totals; original truncated attempt remains excluded |
| [First zero](2026-09-06/first-zero-analysis.json) / [first one](2026-09-06/first-one-analysis.json) | Original strict-checker failures, including the unchanged 1,250 ns period limit |
| [Independent zero anomaly](2026-09-06/first-zero-anomaly.json) | Raw-edge phase and symbol review, with source binary hash |
| [Edge excerpt](2026-09-06/anomaly-edge-excerpt.csv) / [symbol excerpt](2026-09-06/anomaly-symbol-excerpt.csv) | Exact forensic CSV excerpts |
| [Stopped userspace scout](2026-09-06/userspace-scout-summary.json) | Retained failing endurance outcome |
| [Early protected review](2026-09-06/early-protected-summary.json) | First longer protected zero/one captures and GPIO0 timing |
| [Cadence comparison](2026-09-06/cadence-summary.json) | Existing timing comparison with process details removed |
| [Protected short/loaded captures](2026-09-06/protected-short-captures.json) | Smoke, matrix, signal-capture, and seven-phase loaded-scout results |
| [Admission failure review](2026-09-06/admission-failure-final-capture.md) / [data](2026-09-06/admission-failure-final-capture.json) | Original stopped run's last clean capture and rejected next request |
| [Admission failure mailbox](2026-09-06/admission-failure-mailbox.json) | Exact retained DONE/output state after the rejection |
| [Controlled storage captures](2026-09-06/storage-admission-captures.json) | Zero/one captures after admission-policy revision |
| [Software/live checks](2026-09-06/software-and-live-checks.json) | Original result lines and source line numbers; initial and final stages separate |
| [Kernel probe](2026-09-06/kernel-probe-events.jsonl) | Exact direct-ioctl rejection and fatal-sender cleanup log |
| [PRU audit](2026-09-06/pru-audit.txt) | Exact final 139-frame machine-code audit result |
| [Earlier build identity](2026-09-06/earlier-build-identity.json) | Compiler, size, and hash excerpts; its module predates the final module |
| [Historical ABI3 checks](2026-09-05/historical-checks.json) / [spin benchmark](2026-09-05/spin-benchmark.txt) | Original reference-board software/lifecycle/handover result excerpts and exact timing samples |

The final kernel module SHA-256 is
`1e9aa2b967c5930d820989a0c76acc1f7a88eafce6ff184f156ea49687753414`,
with loaded `srcversion` `A68C9E83214A669AA2A7069`, from the handback record.
The earlier build report's module is a different revision. Its firmware and
CLI hashes match the final recorded artifacts; the firmware SHA-256 is
`da45899a51fdedbf80ec2a7d60c785f5be82970caf115f807b54719661084a60`.

## Local archive inventory

The detailed archive was preserved locally under the following
repository-relative paths and is intentionally ignored by Git:

- `build/bbg/`: historical native build, disassembly, suite results,
  bring-up fault/restoration diagnostics, and delivery archive.
- `build/bench-20260906/legacy-001/`, `legacy-002/`, `legacy-003/`:
  original legacy captures and reports.
- `build/bench-20260906/first-zero-30s/`, `first-one-30s/`, `scout-001/`:
  userspace failures, raw exports, native capture archives, and forensic work.
- `build/bench-20260906/protected-*/`: protected matrix, smoke, signal,
  initial longer captures, loaded scout, and both overnight runs.
- `build/bench-20260906/admission-storage-*/` and the adjacent writer logs:
  controlled storage admission checks.
- `build/bench-20260906/final-build-evidence/`: native build and audit
  reports; the report's module identity predates the final admission revision.
- `build/bench-20260906/overnight-aggregation/`: original complete final
  JSON/Markdown accounting and earlier immutable snapshots.
- `build/bench-20260906/handback-final.json`, `handback-windows.json`,
  `overnight-final-operational-metrics.json`, and `plan.md`: original final
  records, with detailed local operational information retained only there.

The manifest supplies hashes for the selected source files and representative
raw artifacts, including the first zero anomaly binary and the final supplement's
six exports and native capture. The final accounting includes original analysis
identities for all reviewed overnight captures. It does **not** claim a complete
hash inventory of every multi-gigabyte raw capture, nor include those files in
this repository. To verify an available original or curated record, compute
SHA-256 over its bytes and compare with the corresponding manifest entry.

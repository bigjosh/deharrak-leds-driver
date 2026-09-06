# Driver validation

The tests separate software behavior from physical waveform qualification. A
passing software test does not qualify the installed LED type, wire color order,
propagation allowance, or signal measured after the level shifter.

## Tests without GPIO or PRU access

Run from the project directory on the native build board:

```sh
make test-native
```

Use the same `LOCK_PATH=/absolute/project/dld.lock` argument used for the build,
if it was overridden. `test-native` depends on `all`, so it also builds the ARM
commands, firmware, and module against matching installed kernel headers. It
does not load the module or initialize hardware. `make test` additionally runs
the PRU machine-code model and linked kernel instruction audit. The supported
build environment and clean/rebuild rules are in the [build guide](../docs/build.md).

`test_common.c` uses pure functions from `src/dld_common.c`; it does not map devices
or load firmware. It covers color syntax and byte order, strict JSON configuration
including escaped names and duplicates, physical pin-to-bank mapping, zero and
unequal lengths, timing-budget bounds, and mailbox readiness across sequence
wraparound. The settling assertions document the initial engineering values of
1 microsecond per forwarded pixel and 100 microseconds of margin; they are not
measurements of the hardware.

`test_admission.c` exercises the same callback-based admission policy used by
the kernel, with fake clock and hardware actions. Its 17 assertions cover busy
storage becoming idle after 75 ms, a full one-second timeout, the independent
10,000-attempt limit when time stops advancing, and the rule that no granted
result is retried. No grant occurs while admission reports busy. This tests the
policy, not actual peripheral idleness or Linux scheduling.

`test_send_syscall.c` compiles the actual `dld-send` entry point and shared
`dld_sender` module with device and lock boundaries replaced by stubs. It
retains the five original syscall outcomes, including preserving a valid
session after definitive rejection and cleaning up an uncertain result after
a simulated completed frame. Resident-sender checks cover repeated sends with
one attachment, per-frame locking, fresh configuration after reinitialization,
intervening sequence advancement and wrap, busy/invalid mailbox rejection,
prepublication cancellation, critical-result cleanup, failed-context reuse,
and closing partial attachments. It opens no devices or lock files and
requires no privileges.

`test_opc.c` exercises the pure UDP payload parser: all channel/command values,
header and declared-length boundaries, big-endian length, the largest normal
IPv4 datagram payload, first-pixel selection, and ignored trailing messages.
Rejected packets must leave the caller's selected color untouched. It opens
no sockets or devices.

`test_flash.c` tests the pure elapsed-time frame selector and inactivity
predicate with supplied timestamps. It checks both ramps, channel rounding,
initial/peak/final endpoints, sends that skip one or both deadlines, clock
limits, restart state, and strict expiry after 60 seconds. It opens no clocks,
sockets, files, or hardware devices.

`test_udp.py` runs the real receiver/socket loop linked with
`tests/fake_udp_sender.c` instead of the hardware sender. It uses temporary files
and local loopback UDP sockets, with no device access or BBG connection.
Tests cover malformed/unsupported packets, retained attachment, repeated
colors, bounded coalescing, paced 20 Hz input with both faster and slower fake
senders, IPv4/IPv6 and IPv4 broadcast reception, exclusive binding, idle signals, fatal sender
errors, in-flight cancellation, and option/help validation. The harness uses
POSIX signals including `SIGSTOP`/`SIGCONT`, so run it on Linux; it supports
the board's Python 3.2 and needs no extra Python packages. Its simulated 20 Hz
cases test the receiver and queue policy, not actual hardware throughput or Ethernet loss.

The packet-only cases disable both status flashes to preserve their original
input-to-send assertions. Status-flash checks separately cover default and
independently disabled behavior, black/full-color/black endpoints, elapsed-time
brightness with slow sends, inactivity recurrence, and activity from rejected
packets. They also exercise valid-packet takeover between flash frames and
the existing signal/fatal-error rules. The test executable alone wraps
`clock_gettime`; an offset file lets the harness advance inactivity time
without waiting a real minute, and simulate clock-read failure. The product
has no clock override or configurable flash duration. Simulated send duration
and time do not establish the animation's appearance on the installed panel.

These checks are included in `make test-native`. To rerun just the packet,
flash, and socket tests after building their targets:

```sh
build/test-opc
build/test-flash
python3 tests/test_udp.py build/test-udp
```

`test_cli.sh` takes one build-directory argument and invokes the actual commands
only with invalid syntax, invalid configuration contents, or a nonexistent
configuration file. It verifies rejection codes and diagnostics without submitting
a valid initialization or color request. It creates a unique temporary directory
beneath that build directory and removes only its own scratch files.

```sh
sh tests/test_cli.sh "$(pwd)/build"
```

Review the CLI call paths alongside these tests to confirm that parsing and file
validation precede locking and device access. The rejection tests alone are not
an instrumentation-based proof that no device was opened.

Three additional Python suites are invoked explicitly, not by `make test`:

```sh
python3 tests/test_bench_sender.py
python3 tests/test_bench_storage_load.py
python3 tests/test_analyze_capture.py
```

The sender suite uses fake clocks/commands and supports the board's Python 3.2
without extra packages. The storage suite uses a fresh local temporary file;
it performs two real `fsync` calls totaling 128 KiB, with no board connection.
Its Windows tests do not establish Linux `O_NOFOLLOW` protection. The capture
checker uses generated files and requires the modern host Python/NumPy
environment in [the checker guide](../tools/analyze_capture.md); it is not a
Python 3.2 board test. It covers corrupt exports, pulse windows, reset/frame
boundaries, colors, bank order, and shared-prefix edge alignment.

Do not use broad `unittest discover` over `test_*.py` for Windows offline checks:
that also imports the Linux UDP harness and separately invoked live harnesses,
which require POSIX facilities such as `fcntl` and Unix signals. None of the
three explicit offline commands above contacts BBG, Saleae, or the Logic
application.

## Temporary SSH deployment checks

Run the remote-handover and Linux-launcher software tests explicitly:

```sh
python3 tests/test_trial_remote.py
python3 tests/test_deploy_trial.py
```

The remote-handover suite supports Python 3.2 and later without extra packages,
including on Windows. It replaces
system, subprocess, service, kernel, and process observations with controlled
test doubles and writes only temporary local files. It covers bundle integrity
and unsafe archive entries, prerequisite checks before runtime changes, failure
ordering during handover, and detached receiver readiness and liveness.
It does not connect over SSH, run native DLD commands, load a module, stop
LEDscape, or access GPIO/PRU hardware.

`test_deploy_trial.py` runs nine POSIX launcher cases with fake `ssh` and `scp`
executables placed first on a private test PATH. It checks transfer/handover
ordering, paths containing spaces, numeric IPv6 and flash options, strict host
verification, rejected arguments and unsafe remote-directory responses, and
failure propagation. It runs on Linux and skips on Windows; it never contacts
a target. Windows PowerShell argument handling needs a separate native
fake-executable check for the PowerShell versions being supported.

Creating a [trial bundle](../docs/trial.md#build-the-bundle-once) compiles in a
fresh native workspace. That checks build compatibility; executing either
`deploy-trial` launcher performs an actual hardware handover and belongs in a
separately authorized live test. Neither mocked startup nor a successful
readiness message establishes the installed panel's waveform, appearance,
or delivery rate from the real controller.

## Native build isolation and read-only inspection

Use the existing compiler, make, assembler, and libraries on the target. Place the
source copy and every build/test output in a newly created project directory.
Do not install packages, overwrite existing files, alter system tools or service
definitions, or build inside the existing LEDscape tree. Creating these test
artifacts does not require changing the running LED controller.

Before a live test, a read-only inspection can record the kernel/userspace versions,
tool paths and versions, PRUSS UIO device metadata, GPIO pinmux/direction state,
CPU frequency limits, and existing LEDscape service mechanism and color-order
configuration. Reading this information is separate from changing hardware state.
Do not execute a valid `dld-init` or `dld-send`, export GPIOs, stop services, or load
firmware merely to collect build information.

Inspect the final PRU assembler listing and linked kernel disassembly. Verify PRU
instruction-memory size, all symbol paths, bank gates and pixel-boundary mask
changes, plus the kernel's Thumb entry and warmed ARM grant/PMU loop.
`make -C kernel audit` checks exact emitted instructions, relocations, the
independent iteration cap, and deliberately corrupted audit inputs. The old
`bench-spin` countdown benchmark is historical and does not drive ABI4 sends.
These audits supplement the pure-function budget tests.

## Later isolated hardware validation

Run live tests only in the agreed test window. The external wrapper must stop the
actual LEDscape service, prevent its restart during the test, and call `dld-init`
only after successful shutdown. The core CLI commands do not scan or manage
services. Keep the wrapper, panel configuration, logs, and captures in the new
test directory; no production boot or existing service-file edits are part of this
test plan.

Before any live suite, apply the fixed-1-GHz/user-LED preparation and load the
matching `dld_quiet.ko`, following [driver startup](../README.md#start-the-driver)
and [quiet-window prerequisites](../docs/quiet-window.md). The module requires
a running `eth0` whose parent is bound to `cpsw`, an available exclusive PMU
event, and the documented absence of competing autonomous masters. These
requirements apply even when all six lengths are zero. The harnesses neither
establish ownership nor load/unload the helper. Do not build or run another
test/sender alongside an exclusive physical capture.

Stop `dld-udp` before running the existing direct-CLI live suites. For a
separately authorized UDP qualification run, initialize once, start only the
receiver, and drive the actual network input. Correlate controller timestamps,
local received/coalesced/completed counts, and physical captures at 20 Hz,
under burst traffic, and with the agreed background loads. Include both zero
and one bits. Use `--no-startup-flash --no-idle-flash` for packet-only waveform
measurements. Separately check the default green startup and red inactivity
flashes on the panel, including intermediate levels, full-color endpoints,
black completion, and takeover when normal traffic resumes. Record packet
loss and capture gaps; the shim has no remote
acknowledgment, and local counters cannot count frames dropped before socket
reception. Do not equate the prior CLI endurance results or fake-sender
loopback tests with completed UDP hardware qualification.

Use short, controlled runs to check all-output-low initialization, each pin's
mapping, exact unequal string lengths, profile byte order, final settling, and
repeated use after initialization. Check command-lock contention, a published
request whose sender died, and the distinction between busy pre-submission
rejection and critical post-spin failure. Fault-injection tests must preserve the
chosen rules: no automatic frame or granted-bank retry, no extra per-send
hardware identity/liveness probe, and explicit reinitialization
after critical completion failure. Do not inject faults into the production
LEDscape process or alter the existing deployment to simulate them.

## Oscilloscope endurance procedure

For physical timing and endurance, follow [spec.md](../spec.md) sections
13.4–13.8. After the exclusive handover, initialize once with all six lengths
300. Arm the positive pulse-width trigger at greater than 400 ns, then run:

```sh
sh tools/endurance.sh 000000 10800
```

Stop sending, change/rearm the trigger to greater than 800 ns, then run:

```sh
sh tools/endurance.sh FFFFFF 10800
```

This shell wrapper sends sequentially and checks its three-hour limit between
commands. It stops on a command error, but has no child-process watchdog: a
hung command can exceed the requested duration. Use the logged Python sender
below when a command watchdog or absolute UTC cutoff is required.

Record the actual pin, profile, measurement point, duration, cadence, trigger
setup, and capture gaps. A clean run means no qualifying high pulse was observed
on that pin. These trigger settings do not test short pulses, data lows, bit
periods, or all of the checker's ±50 ns windows; use
[captured-edge analysis](../tools/analyze_capture.md) for those checks. Neither
procedure establishes continuous observation through acquisition gaps or, on
its own, complete request-to-frame correlation.

The kernel excludes ordinary ARM execution and quiesces the checked DMA engines
while pixel data is emitted. Instruction fetches still exist; the small loop is
warmed in cache before the grant. Physical captures remain the acceptance test.
The earlier userspace-only implementation's timing violations and the completed
protected run are retained in the [bench report](../docs/validation-20260906.md).

## Explicit live lifecycle harness

`hw_probe.c` and `test_hardware.py` are separate test helpers; neither is invoked
by the product commands or `make test`. Build `hw-probe` with the same
absolute project-local `DLD_LOCK_PATH` as the product commands. For example, after the normal
native objects have been built in the new project directory:

```sh
make LOCK_PATH="$PWD/dld.lock" build/hw-probe build/quiet-kernel-probe
```

Only after the operator has stopped LEDscape and prevented restart, explicitly run:

```sh
python3 tests/test_hardware.py --run-live /absolute/new/project/build
```

The Python harness supports Python 3.2. It retains configurations, command output,
JSON register snapshots, and results in a new `hardware-test-*` directory beneath
the specified build directory. It does not stop or restart services, change
existing system files, or install packages. The probe's `snapshot` command reads
mailbox, PRU control, and GPIO registers; GPIO mappings are read-only. It takes
the shared command lock, creating that project-local lock file if needed. Only
explicit `--run-live` probe subcommands can stop idle PRU0 or inject an outstanding
request/status into its owned mailbox while PRU0 is stopped. The additional
`--run-live withhold-first-grant` subcommand publishes a test request to the
running DLD firmware while withholding all bank grants; it checks WAIT_BANK
without emitting pixel data. Probe fault actions never manipulate PRU1 or
GPIO, load firmware, or add identity checks to the product commands. Product
CLI calls within the surrounding harness do perform initialization,
transmission, and failure cleanup.

The harness checks zero, unequal, and maximum lengths; retained configuration and
sequence progression; all four diagnostic profile byte orders in the mailbox; PRU
enable state; low output latches and output directions; malformed-input and lock
rejection; stopped READY/DONE causing critical cleanup; and stopped outstanding
requests remaining busy without invalidation. Reinitialization is an explicit
test action. All three fixed bank ordinals are also checked with their first
grant deliberately withheld: the PRU must remain low and busy until explicit
initialization. A passing run leaves a READY configuration with six zero lengths.
A failing run stops and retains its current state for operator recovery; it does
not retry a failed product command. A 15-second harness watchdog terminates a
hung child, with a kill fallback after one second, then fails the suite. That
watchdog does not change the product's completion rule or test cancellation.

By default, GPIO0/1/2 snapshots compare every unrelated OE and DATAOUT bit.
Concurrent kernel LED activity or other GPIO owners can make those comparisons
fail; inspect the recorded changed bits instead of attributing them to this driver
automatically. After confirming that the board's four kernel user LEDs own
GPIO1 bits 21-24, the operator may explicitly add `--allow-kernel-user-leds` to
the harness invocation. This excludes only those four DATAOUT bits, mask
`0x01e00000`, from observational comparisons. The run log records the mask and
observed excluded differences. All OE bits and every other DATAOUT bit remain
checked, including the six driver output bits for unchanged-state assertions.
The flag does not disable or change kernel triggers. It makes no hardware
preservation claim for excluded user-LED DATAOUT bits; constraints on driver
writes to those bits remain checked by source review and the PRU machine-code
audit.
Successful endpoint snapshots do not prove absence of transient writes or pulse
timing, physical byte order, exact string length, or final LED reset duration.
Those remain waveform/emulator and hardware qualification checks. Live sequence
wrap is deliberately omitted: changing both mailbox counters alone would leave
the PRU's accepted-sequence register inconsistent; the focused emulator test
covers wrap without fabricating a live protocol state. Sender signals have their
own separately invoked suite below.

## Explicit live sender signal tests

After the same exclusive hardware handover, run this separate Python 3.2 suite:

```sh
python3 tests/test_signals.py --run-live /absolute/new/project/build
```

Add `--allow-kernel-user-leds` only if those GPIO1 owners have been confirmed
and the stated observational exclusion is intended.
The suite initializes all six lengths to 300 and sends only black. It signals
the actual `dld-send` child after a default 20 ms delay, requires that the child
was still alive, and then verifies request-sequence advancement in a snapshot
taken only after the child exits and releases its lock. A scheduling race that
misses publication or reaches an already exited child fails diagnostically;
the script makes no automatic retry. `--signal-delay-ms` explicitly selects a
different 1..50 ms delay if the operator needs a separate test run.

For `SIGKILL`, the child must exit from that signal without a success message.
After one fixed 100 ms observation interval, its retained request must be DONE
with the matching completion sequence and intact configuration. A subsequent
send must succeed without initialization. With ABI4, the in-flight synchronous
kernel operation continues granting the remaining banks before process teardown;
the PRU cannot independently cross an ungranted bank boundary. For `SIGTERM`, the child must return
critical code 5 with a cancellation diagnostic, leave an invalid mailbox, stop
both PRUs, and leave all six output latches low and directions output. A further
send must reject with code 7; explicit initialization and a successful send then
verify recovery. A passing suite finishes with a READY all-zero-length session.

The signal suite reuses the lifecycle helpers and retains all files beneath a
new build-directory test folder. It manages no services and changes no system
files. A 15-second watchdog kills a stuck child and fails the suite. Exact signal
timing is subject to Linux scheduling: the evidence establishes publication and
host interruption, without establishing which PRU instruction or waveform phase
was active at that instant. These tests do not qualify physical pulse widths,
chain settling, or the several-hour endurance requirement.

## Direct kernel rejection and fatal-sender cleanup

Build `make build/quiet-kernel-probe` with the same `DLD_LOCK_PATH` as the CLIs.
Load the helper and explicitly initialize a fresh READY session with all six
lengths zero, then run:

```sh
build/quiet-kernel-probe --run-live /absolute/new/probe-artifact-directory
```

The probe holds the shared lock, checks raw ioctl rejection without changing
mailbox or GPIO state, and stops idle PRU0 using the graceful control sequence.
It forks a raw ioctl sender, observes publication, and sends SIGKILL before the
kernel's 20 ms final-completion timeout (the all-disabled request has no bank
gates). The kernel must invalidate the mailbox and stop the PRUs
despite the child never returning to userspace. The probe calls no product cleanup
and performs no automatic initialization. Both success and failure leave the
board for explicit recovery; a successful test leaves an invalidated session.
It begins with output latches low, so it does not establish a high-to-low cleanup
transition. Results include publication/signal timing and the observed register
state, in a newly created `events.jsonl` artifact.

## Logged traffic and capture automation

After an exclusive handover, the Python 3.2 traffic generator can initialize once
and execute a bounded sequence of normal CLI commands. Its output directory
must be new and absolute, with an existing parent. For example, from a freshly
built project directory on the prepared board:

```sh
python3 tools/bench_sender.py --require-quiet --build-dir "$PWD/build" \
    --output-dir "$PWD/build/zero-run-001" --profile ws2812b \
    --lengths 300,300,300,300,300,300 --mode fixed --color 000000 --duration 60
```

Other modes are `cycle` (the fixed schedule retained in the manifest) and
`random` (seeded xorshift32 colors, selected with `--seed`). Use `--count` for
a successful-send limit; `--deadline-utc YYYY-MM-DDTHH:MM:SSZ` reserves time
for command termination before an absolute cutoff. `--stop-file /absolute/path`
stops between commands when that file exists. `--skip-init` requires the exact
recorded profile/lengths to have been initialized by the operator. Without it,
the generator replaces initialization once at the start. No flag retries a
failed command, reinitializes after an error, or manages services.

The default per-command watchdog is seven seconds, followed by one second
after terminate and then one second after kill. It stops the run on any error
or hang and retains the reported hardware-state uncertainty. This is a harness
limit, not a longer PRU completion grace period. Successful sends are flushed
to compact `successes.bin` timestamp records; `manifest.json` contains the
color schedule, and `events.jsonl` progress records plus `summary.json` retain
counts, failures, and reported protected-window metrics. Duration/count/stop-file
limits are checked between commands and do not truncate an in-flight frame.

`bench_capture.py`, `bench_matrix.py`, and `bench_soak.py` are separate Windows
bench automation, not portable deployment commands or part of `make test`.
They require a modern host Python, Logic 2 with its Automation API enabled,
the `logic2-automation` package, and the offline checker's NumPy dependency.
The [checker setup](../tools/analyze_capture.md) installs the tested host
versions from [requirements-bench.txt](../requirements-bench.txt) into a venv
without relying on PowerShell activation policy.
The current scripts contain bench-specific device serial, channel mapping,
threshold, network target, drive/free-space and timing assumptions; review
their arguments/source before a new authorized hardware session. Capturing
does not establish service/PRU ownership. Preserve the native `.sal`, raw
exports, metadata, sender journal, and checker reports, and record acquisition
gaps. In the soak supervisor, cleanup is allowed past its configured deadline;
an external handback cutoff therefore requires earlier STOP and verified
sender/load/capture exit. The completed September 6 run is documented as
[historical validation](../docs/validation-20260906.md).

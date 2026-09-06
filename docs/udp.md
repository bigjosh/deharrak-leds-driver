# LEDscape UDP compatibility

`dld-udp` lets the existing controller drive DLD using its current OPC-over-UDP
packets. It takes the first RGB pixel from each accepted packet and sends that
color uniformly to every enabled pixel. The standalone `dld-send` command
remains available, and both programs use the same internal sending code.

## Start and stop

Build and complete [driver startup](../README.md#start-the-driver), including
successful `dld-init`, before starting the receiver as root:

```sh
build/dld-udp
```

The default bind is `::` on UDP port 7890, with IPv4 reception enabled on that
socket as well. To select IPv4 explicitly or choose another port:

```sh
build/dld-udp --bind 0.0.0.0 --port 7890
build/dld-udp --bind 192.168.1.50 --port 7890
```

Use a numeric IPv4 or IPv6 address assigned to the board, or a wildcard address;
hostnames are not accepted. `--port` accepts integers from 1 through 65535.
Run `build/dld-udp --help` for usage without opening devices. The receiver
accepts IPv4 unicast and broadcast through the normal local socket/network
configuration. It binds exclusively: another listener on the same endpoint
is a startup error. It does not change interface, routing, firewall, or service
configuration. Binding a socket does not establish ownership of the LED pins;
the normal DLD preparation and handover requirements still apply.

The process stays in the foreground. Ctrl+C, `SIGTERM`, or `SIGHUP` stops it;
an idle stop sends no frame and leaves the initialized PRU and last displayed
color alone. No traffic timeout or demo pattern changes an idle panel. If a
handled signal interrupts an active send, the shared sender follows the same
critical cancellation and cleanup rules as `dld-send`. A submitted kernel
operation retains ownership until it finishes or cleans up its failure.

Stop the receiver and wait for it to exit before manual testing, changing panel
configuration, unloading `dld_quiet`, or [returning control to LEDscape](../README.md#return-control-to-ledscape).
An open receiver retains the helper's device reference. Configuration changes
still use `dld-init CONFIG_FILE`; restart the receiver after successful
initialization. Production boot-service installation and restart policy are
not supplied by this shim.

## Accepted packet format

Each UDP datagram begins with this four-byte OPC header:

| Byte offset | Meaning | Requirement |
|---:|---|---|
| 0 | OPC channel | Ignored; any channel is accepted |
| 1 | OPC command | Must be zero, set pixel colors |
| 2–3 | Payload byte length | Unsigned big-endian; at least three |
| 4 | First pixel red | 0–255 |
| 5 | First pixel green | 0–255 |
| 6 | First pixel blue | 0–255 |
| 7 onward | Remaining payload/pixels | Must be present to the declared length; ignored |

The complete declared payload must fit in the received datagram. A short
header, unsupported command, payload shorter than one RGB pixel, or truncated
payload is discarded without submitting a frame. Extra bytes after the first
declared message are allowed and ignored; a second OPC message in the same
datagram is not processed. A payload need not be a multiple of three because
only its first complete pixel is used.

For example, `00 00 00 03 FF 80 00` requests logical RGB `FF8000`. DLD then
applies the initialized panel profile's wire color order. The packet does not
carry pin enables, string lengths, a pixel profile, or waveform timing.

From another machine with Python 3, this sends that single-pixel orange packet:

```python
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(bytes.fromhex("00 00 00 03 FF 80 00"), ("beaglebone", 7890))
sock.close()
```

No TCP listener, OPC system-exclusive command, per-pixel output, brightness
transform, gamma correction, dithering, interpolation, or animation fallback
is implemented. This is compatibility with the legacy first-pixel color input,
not a replacement for LEDscape's general-purpose rendering features.

## Update rate and queued packets

The receiver waits for a datagram, then drains up to **64 datagrams total** in
that batch without blocking. It sends the newest valid color found in the
batch. An invalid packet never replaces an already selected valid color. If
there is no valid packet, it waits for more data without touching the sender.
The bound prevents a continuous flood from postponing transmission forever;
larger bursts can require several batches and are not guaranteed to skip
directly to the newest packet in the entire socket queue.

It completes that send, including the final PRU settling wait, before processing
the next batch. It stores one selected color rather than maintaining a separate
frame queue. Repeated identical colors are submitted again; there is no deduplication.
Selection follows receive order, since OPC/UDP has no sequence number to detect
network reordering or stale retransmissions. Packets from all senders compete
on the same basis.

The socket and kernel queues can drop packets, including while Ethernet DMA is
paused for a protected bank. The receiver cannot count datagrams that never
reach its socket. There are no acknowledgments or retries, so a successfully
sent UDP datagram does not prove a panel update. The panel retains its last
latched color when no further frame arrives.

At 20 Hz there are 50 ms between updates. Keeping resources open removes
process startup and remapping overhead, but does not establish a 50 ms output
deadline. Linux scheduling, per-bank quiet windows, Ethernet recovery, and
the existing bounded DMA admission waits still contribute to latency. The
target's sustained receive-to-completion rate and loss behavior need testing
with the installed controller and workload. The previous CLI waveform endurance
results do not establish this new receiver's throughput.

## Shared sender and errors

The internal `dld_sender` module holds the PRU mapping, `/dev/dld-quiet`
descriptor, and command-lock descriptor open for the process lifetime.
For each send it takes the same nonblocking lock as `dld-init`/`dld-send`, reads
and validates the current mailbox/configuration, submits logical RGB with that
configuration to the protected ioctl, checks completion, and
releases the frame lock. It rereads no JSON file and starts no child process.
Keep the shared lock file in place while any sender is attached; deleting or
replacing it would let a new process lock a different file from the resident sender.
The kernel applies the profile's wire color order.
There is one transmission implementation, with the existing kernel and PRU
protocol unchanged.

The per-frame lock permits cooperating commands between UDP frames; it does
not coordinate competing applications' desired colors. A conflict can make
the receiver exit busy, so stop it before manual sends or reinitialization.
The receiver never keeps using a cached profile after a successful coordinated
reinitialization: the next send validates the new mailbox configuration.

A sender failure is fatal to the receiver, including a busy or invalid session.
It reports the diagnostic and exits using the sender's existing
[exit-code meanings](../README.md#handle-errors). It does not drop a failed
send and continue, initialize implicitly, or automatically retry the frame.
Inspect the error, resolve ownership/prerequisites, and explicitly reinitialize
after a critical/invalid-session error before restarting. Invalid network
packets alone are discarded and do not terminate the process.

Startup and shutdown diagnostics go to stderr; normal operation writes no
stdout and logs no per-frame success line. On exit the receiver reports:

| Counter | Meaning |
|---|---|
| `received` | Datagrams read from the socket |
| `valid` | Datagrams with an accepted first-pixel message |
| `malformed` | Invalid/truncated packets |
| `unsupported` | Packets using an unsupported OPC command |
| `coalesced` | Earlier valid colors replaced within a receive batch |
| `sent` | Frames the shared sender completed successfully |

It also records the caught signal and exit code. A selected frame abandoned by
shutdown or a sender error can make `valid - coalesced` greater than `sent`.
These are local counters, not remote completion acknowledgments or a count of
network losses. Keep logs outside the timing capture interval if they would
add storage activity. The
[test guide](../tests/README.md) separates software packet/lifecycle checks from
future live packet-loss, cadence, and waveform qualification.
The [implementation validation record](validation-udp.md) records the native
build, software results, and artifact identities.

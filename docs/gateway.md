# Deploy through a Raspberry Pi gateway

A Raspberry Pi can download a GitHub release and deploy DLD to BBGs on its own
network. It needs outbound HTTPS to GitHub and SSH access to the target BBGs.
Neither the Pi nor those BBGs need to accept connections through the site's NAT.
The Pi stores the release and runs its shell launcher; the included ARMv7
executables and kernel module run on the BBGs. There is no compilation on the Pi.

## Download the first release

The [repository](https://github.com/bigjosh/deharrak-leds-driver) and its releases
are public. No GitHub account, login, token, or GitHub CLI is required. Install
the download and SSH tools on Raspberry Pi OS if needed:

```sh
sudo apt-get update && sudo apt-get install -y curl openssh-client ca-certificates
```

Download and verify the versioned release, then unpack it into a new directory:

```sh
(
  set -eu
  umask 077
  [ ! -e "$HOME/dld-gateway-v0.1.0" ] || { echo 'Already installed: ~/dld-gateway-v0.1.0' >&2; exit 1; }
  download=$(mktemp -d "$HOME/dld-download.XXXXXX")
  release_url=https://github.com/bigjosh/deharrak-leds-driver/releases/download/v0.1.0
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --output "$download/dld-gateway-v0.1.0.tar.gz" \
    "$release_url/dld-gateway-v0.1.0.tar.gz"
  curl --fail --location --proto '=https' --proto-redir '=https' \
    --output "$download/SHA256SUMS" "$release_url/SHA256SUMS"
  cd "$download"
  sha256sum -c SHA256SUMS
  tar -xzf dld-gateway-v0.1.0.tar.gz -C "$HOME"
  printf 'Gateway ready: %s/dld-gateway-v0.1.0\n' "$HOME"
)
```

This only stages files on the Pi. The downloaded archive and checksum remain in
the printed installation's neighboring `dld-download.*` directory. To update,
use the new release tag and archive name; each version has its own directory.

The original `v0.1.0` archive contains an older guide that mentions private-repo
login. That step is obsolete; use this guide for public downloads. The original
archive and its checksum are unchanged.

## Configure and deploy a panel

Targets must be compatible BBGs running **Linux 3.8.13-bone80, ARMv7**, with the
runtime prerequisites in the [trial guide](trial.md). The Pi's own kernel and
32/64-bit architecture do not need to match. The Pi's OpenSSH client must support
`scp -O`; current Raspberry Pi OS releases provide it. BBG root login must work
without an interactive password prompt.

Create a separate local configuration for each panel. Copy the example and edit
the pixel profile and all six lengths before deploying:

```sh
cd "$HOME/dld-gateway-v0.1.0"
mkdir -p panels
cp -i config/panel.example.json panels/panel-a.json
nano panels/panel-a.json
```

The example is `ws2812b` with six strings of 300; it is not a detected setting for
your remote panels. A zero length disables a string. Supported profiles are
`ws2812b` (GRB), `ws2811-hs` (RGB), and their `-bgr` variants. If replacing
LEDscape, inspect its local configuration to establish the installed color order
and lengths. See the [configuration guide](configuration.md).

Then supply the actual BBG address on the Pi's network:

```sh
sh ./dld-deploy 192.168.1.50 panels/panel-a.json
```

The launcher accepts SSH host keys automatically, uploads into a new BBG `/run`
directory, validates the bundle and configuration, stops the existing receiver
and LEDscape, replaces the helper, initializes, and starts `dld-udp`. Repeating
the command replaces an existing DLD trial. The optional flash and bundle flags
from the [trial guide](trial.md) are passed through unchanged.

The BBG handover remains temporary: it changes no installed service/configuration
files or boot enablement. Reboot restores the existing LEDscape boot setup.
Persistent DLD installation at BBG boot is a separate procedure. A different
BBG kernel requires a matching native build, not a change to the Pi installer.

## Release contents and verification

The archive includes the matching native bundle and bootstrap, Linux/Windows
launchers, example configuration, operating/history documentation, and the local
[scene exerciser](exercise.md). `release.json` records file hashes, native-bundle
hash, target requirements, and the gateway source commit. No private keys or
machine-specific live panel configurations are included.

To reproduce a gateway archive from a checkout with an existing tested native
bundle, using Python 3.8+ on the build/packaging computer:

```sh
python tools/package-gateway.py --version v0.1.0 --output-dir build/releases/v0.1.0
```

The packager checks all native payload hashes and requires the bundled bootstrap
to match the local launcher. It uses an allowlist and refuses to replace existing
release files. The SHA-256 sidecar detects transfer corruption; it is not an
independent signature. Download both files from this repository's GitHub release
over HTTPS with certificate verification enabled, as the commands above do.

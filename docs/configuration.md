# Panel configuration

Each panel uses one pixel profile and six string lengths. `dld-init CONFIG_FILE`
reads the local JSON file and stores the configuration in PRU memory. The
standalone sender and UDP receiver use that retained state until the next
initialization; they do not reopen the file for each color.

Start from [the example](../config/panel.example.json). For six 300-pixel
WS2812B strings with GRB wire order:

```json
{
  "pixel_type": "ws2812b",
  "string_lengths": [300, 300, 300, 300, 300, 300]
}
```

Use a separate file for panels with different profiles or lengths. The
[deployment scripts](trial.md) copy the file you supply; they do not detect
the hardware or infer settings from LEDscape. When replacing an existing
panel, use its local LEDscape configuration and known hardware to establish
the correct color order and lengths.

## String lengths and pin order

`string_lengths` must contain exactly six integers from 0 through 300, in this
order:

| Index | Header pin | GPIO |
|---:|---|---|
| 0 | P8_8 | GPIO2[3] |
| 1 | P8_10 | GPIO2[4] |
| 2 | P8_12 | GPIO1[12] |
| 3 | P8_14 | GPIO0[26] |
| 4 | P8_16 | GPIO1[14] |
| 5 | P8_18 | GPIO2[1] |

DLD transmits exactly the configured number of pixels on each enabled string.
For example, `[300, 180, 0, 0, 0, 0]` enables 300 pixels on P8_8 and 180 on
P8_10. A zero length disables transmission and holds that pin low. All six
pins are initialized as outputs, including disabled ones. DLD does not set
pinmux; the pins must already be configured as GPIOs.

Disabling a string does not clear its previously latched color. To turn it
black first, stop the receiver and other senders, send `000000` while the
string is still enabled, then change the file and reinitialize. That send
turns every currently enabled string black.

## Pixel profiles and color order

All pixels on one panel use the same profile:

| `pixel_type` | Wire color order |
|---|---|
| `ws2812b` | GRB |
| `ws2811-hs` | RGB |
| `ws2812b-bgr` | BGR |
| `ws2811-hs-bgr` | BGR |

Command arguments and UDP pixels always use logical **RGB**; DLD applies the
profile's wire order. Check red, green, and blue independently when confirming
an installed panel's profile. Black and white cannot establish color order.
There is currently no GBR profile.

The profile also supplies timing and reset parameters. All current profiles
use nominal 350 ns zero-high, 700 ns one-high, and 1,200 ns bit timing. Reset
and propagation allowances are defined in the [specification](../spec.md).
These are the selected, tested header-waveform targets; LED revision,
downstream electrical behavior, and full-chain settling need qualification
on the installed hardware. See [remaining work](../todo.md).

## Apply or change the configuration

For an SSH trial, edit the local panel file and rerun the
[deployment launcher](trial.md#replacing-an-existing-dld-session). It uploads
and validates the file with the matching bundle before stopping the existing
receiver, then initializes the replacement session and starts its receiver.

For [manual operation](operations.md), stop the calling application and
`dld-udp`, edit the panel file, and run `build/dld-init config/panel.json` from
the prepared runtime directory. Restart the receiver after successful
initialization. Editing the JSON alone has no effect on the active session.
A failed initialization can invalidate the previous session.

Initialization sends no pixel data: holding a pin low does not turn an already
lit panel black. Recovery from an interrupted frame can latch partial data;
it cannot guarantee preservation of the old display. A subsequent successful
color send establishes the requested color on the enabled strings.

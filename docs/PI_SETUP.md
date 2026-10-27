# Setting up the Raspberry Pi enrolment app

## Hardware

* Raspberry Pi 4 (64-bit Raspberry Pi OS, desktop edition), touch display.
* PN532 NFC module on **I²C** (module DIP switches: switch 1 ON, switch 2 OFF), NTAG216 tags.
  The app asserts the chip is an NTAG216 on every tag. NTAG213/215 will be refused — their memory map differs.

| PN532 | Pi |
|---|---|
| VCC | 3.3 V (pin 1) — see "Power" below |
| GND | GND (pin 6) |
| SDA | GPIO2 / pin 3 |
| SCL | GPIO3 / pin 5 |

`sudo raspi-config` → Interface Options → I2C → Enable, reboot, then `sudo i2cdetect -y 1` must show `24`.

## Install

```bash
git clone <repo> ~/dso-asset-register && cd ~/dso-asset-register/pi
bash packaging/build_pi.sh          # builds on the Pi; installs to /opt/dso-enroller and adds a desktop shortcut
```
(A Windows `.exe` cannot run on a Raspberry Pi — the Pi build is a native Linux program started from the shortcut.)
To run from source instead: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && python -m dso_enroller`.

## Configure

Copy the files `scripts/gen_keys.py` produced into `~/.config/dso-enroller/` and lock them down:

```
config.toml   device.key   tag.key   lock.key
chmod 700 ~/.config/dso-enroller && chmod 600 ~/.config/dso-enroller/*
```

`config.toml` (the generator fills this in):

```toml
backend_url = "https://assets.college.edu"       # https is enforced (http only for localhost in dev)
public_host = "assets.college.edu"               # baked into every tag
device_id = "…"                                  # registered on the server
field_recipient_pub = "…"                        # the server's PUBLIC encryption key
device_key_path = "device.key"
tag_key_path = "tag.key"
tag_lock_enabled = false                         # leave false until you've tested on spare tags
reader = "pn532"                                 # "sim" = built-in simulator
pn532_interface = "i2c"                          # or "spi"
page_write_delay = 0.05                          # seconds between page writes; raise if writes are flaky
page_write_retries = 3
write_mode = "library"                           # "raw" = experimental alternative write path
```

The app refuses to start if a key file is readable by other users. Check everything without opening the window:
`dso-enroller --check-config`. Then run it; the first start asks for an operator PIN (6–12 digits).
`dso-enroller --set-pin` changes it from a terminal.

### Auto-start (optional kiosk)

Copy `~/Desktop/dso-enroller.desktop` to `~/.config/autostart/`. The shortcut starts it fullscreen.

## Using it

1. Unlock with the PIN. 2. Tap **Equipment name**, **Product ID**, **Room number** (on-screen keyboard), optionally
**Choose photo…**. 3. **Write tag**, hold a tag flat on the reader. 4. The record is saved on the Pi *first*, then sent;
the chips at the top show reader / server / how many are waiting. If the server is unreachable nothing is lost —
the queue drains when it returns. "Write another tag" keeps the room number.

If a write fails the app says so and **creates no record** — put that tag aside (or just retry with it; an unlocked
tag can be rewritten).

**Tag locking.** Set `tag_lock_enabled = true`, restart: a "Lock this tag permanently" switch appears, and every
lock needs a second confirmation with a 3-second delay. Locking is irreversible and was not exercised on a physical
tag when this was written — **try it on a few spare tags first.**

## Troubleshooting writes

The app logs to `~/.config/dso-enroller/state/enroller.log` (every failed attempt, with the exception). The
final message names the page and attempt count.

* Reads (identify) work but **writes fail** with CRC/timeout errors, often on the first page: this is the classic
  PN532 symptom of a marginal power supply. A write draws more current than a read. Things that help, in order:
  hold the tag **flat, dead-centre, perfectly still**; add a 100–470 µF capacitor across the module's VCC/GND at the
  board; power the module from a supply other than the Pi's 3.3 V pin **with a common ground** (do not run its VCC
  from 5 V into 3.3 V logic without a level shifter); keep wires short; raise `page_write_delay` to 0.15–0.3.
* `vcgencmd get_throttled` should read `0x0` (an under-volted Pi makes everything worse).
* I²C is documented as less reliable than SPI for sustained writes on some PN532 boards. `pn532_interface = "spi"`
  is supported by the driver; SPI0 must be free (a touchscreen overlay such as `ads7846` claims chip-select 0) and
  the module's DIP switches must be set to SPI.
* `write_mode = "raw"` sends the page write through the same command mode as every other operation. It is
  experimental and untested on hardware — use it only to compare.
* The simulator (`--sim`) proves the app and server side are fine, which narrows a failure to the reader.

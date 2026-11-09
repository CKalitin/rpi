# e-Paper clock (raspberrypi, 192.168.1.107)

A Raspberry Pi B+ (Rev 1.2, 32-bit Raspbian 13 "trixie") showing the time and
date on a Waveshare 4.2inch e-Paper module (V2, 400x300, black/white, 8-wire).
A DS3231 real-time clock keeps the time correct without internet, and a status
LED on GPIO 18 blinks while booting and stays on once the clock is drawing.

Pin numbers below are **physical header pins** (1-40) unless written as
"GPIO n", which is the BCM number used in code.

## Dependencies (not in this repo)

The scripts need **Waveshare's e-Paper library cloned at `/home/admin/e-Paper`**.
It is not included here: it's Waveshare's own repo (about 2 GB, unmodified),
and the scripts load the driver and font from that path (`sys.path.insert` and
`FONT` near the top of each script). On a fresh Pi:

```sh
sudo apt install python3-gpiozero python3-lgpio python3-spidev python3-pil i2c-tools git
git clone https://github.com/waveshareteam/e-Paper.git /home/admin/e-Paper
git -C /home/admin/e-Paper checkout a794fbc   # the tested version
git clone https://github.com/CKalitin/rpi.git /home/admin/rpi
```

Then apply the config changes and install the files in `system/` (see
"Configuration changes from a stock install" and "System files" below), and
add the cron entry `@reboot /home/admin/rpi/start_epd_clock.sh`.

## Wiring

### e-Paper display -> Pi (8 wires)

| Display pin | Function                     | Pi header pin | Pi signal          | Set in code by |
|-------------|------------------------------|---------------|--------------------|----------------|
| VCC         | Power, 3.3 V                 | **1**         | 3V3                | -              |
| GND         | Ground                       | **9**         | GND                | -              |
| DIN         | SPI data in (Pi -> display)  | **19**        | GPIO 10, SPI0 MOSI | kernel SPI     |
| CLK         | SPI clock                    | **23**        | GPIO 11, SPI0 SCLK | kernel SPI     |
| CS          | SPI chip select, active low  | **24**        | GPIO 8, SPI0 CE0   | kernel SPI     |
| DC          | Data (high) / command (low)  | **22**        | GPIO 25            | `DC_PIN`       |
| RST         | Reset, active low            | **11**        | GPIO 17            | `RST_PIN`      |
| BUSY        | Panel busy, input, high=busy | **18**        | GPIO 24            | `BUSY_PIN`     |

The GPIO numbers come from the Waveshare driver,
`e-Paper/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py`
(class `RaspberryPi`), which is unmodified from upstream. The display uses SPI
bus 0, device 0 (`/dev/spidev0.0`), mode 0, 4 MHz.

The driver also defines `PWR_PIN = 18`. That is for newer Waveshare boards with
a 9th PWR wire that switches the panel's power. **This module has no PWR wire**;
the panel is always powered from pin 1. `epd_clock.py` takes GPIO 18 over for
the status LED (below).

Waveshare's usual cable colours are VCC grey, GND brown, DIN blue, CLK yellow,
CS orange, DC green, RST white, BUSY purple. Go by the labels on the module,
not the colours, if they disagree.

### DS3231 RTC -> Pi (4 wires)

| DS3231 pin | Function    | Pi header pin | Pi signal        |
|------------|-------------|---------------|------------------|
| VCC        | Power, 5 V  | **4**         | 5V               |
| GND        | Ground      | **6**         | GND              |
| SDA        | I2C data    | **3**         | GPIO 2, I2C1 SDA |
| SCL        | I2C clock   | **5**         | GPIO 3, I2C1 SCL |
| SQW, 32K   | (unused)    | -             | not connected    |

I2C bus 1, address **0x68**. The battery is a coin cell on the module.

> **Check: 5 V supply with I2C pull-ups.** Most DS3231 boards (e.g. the blue
> ZS-042) have pull-up resistors from SDA/SCL to their own VCC. Powered from
> 5 V, that puts about 3.7 V on GPIO 2/3, which are 3.3 V-only pins. The DS3231
> runs fine on 3.3 V, so moving VCC to pin 17 (3V3) or 1 removes the risk.
> Leave it on 5 V only if the board has no pull-ups or has a level shifter.

> **Check: battery charging.** ZS-042-type boards charge the coin cell through
> a resistor and diode beside the holder. That is meant for a rechargeable
> LIR2032. With a plain CR2032, remove the resistor (or diode) or the cell can
> swell or leak.

### Status LED

| Signal     | Pi header pin | Pi signal | Level                              |
|------------|---------------|-----------|------------------------------------|
| Status LED | **12**        | GPIO 18   | 3.3 V output, high = on (see below) |

The LED's other leg goes to GND, for example pin 14 or 20.

A GPIO pin can source only a few mA (keep under ~8 mA). Drive an LED through a
resistor (330 ohm or more), and anything bigger through a transistor or MOSFET.

### Header map

```
                 3V3  [ 1] [ 2]  5V
   RTC SDA     GPIO2  [ 3] [ 4]  5V        RTC VCC
   RTC SCL     GPIO3  [ 5] [ 6]  GND       RTC GND
               GPIO4  [ 7] [ 8]  GPIO14
   EPD GND       GND  [ 9] [10]  GPIO15
   EPD RST    GPIO17  [11] [12]  GPIO18    status LED
              GPIO27  [13] [14]  GND
              GPIO22  [15] [16]  GPIO23
                 3V3  [17] [18]  GPIO24    EPD BUSY
   EPD DIN    GPIO10  [19] [20]  GND
   (SPI MISO)  GPIO9  [21] [22]  GPIO25    EPD DC
   EPD CLK    GPIO11  [23] [24]  GPIO8     EPD CS
                 GND  [25] [26]  GPIO7     (SPI CE1)
                      ... 27-40 unused ...
EPD VCC is on pin 1 (3V3).
```

Pins 21 (GPIO 9) and 26 (GPIO 7) aren't wired, but the SPI driver claims them.
Don't use them for anything else while SPI is on.

## How it starts

1. **~1 s:** the firmware sets GPIO 18 high (`gpio=18=op,dh` in config.txt),
   so the LED is on before Linux starts.
2. **~15 s:** the kernel loads the DS3231 driver and sets the system clock from
   the RTC (`CONFIG_RTC_HCTOSYS`).
3. **~20 s:** systemd starts `epd-boot-blink.service`, which blinks the LED
   (1 s cycle) until `epd_clock.py` claims GPIO 18.
4. cron runs `@reboot /home/admin/rpi/start_epd_clock.sh` (in `crontab -l` for
   admin). That script waits for systemd to finish booting (about 2.5 min
   here, max 3), then for network time sync (max 2 min), then runs
   `epd_clock.py --quiet`, appending output to `~/epd_clock.log`.
5. `epd_clock.py` claims GPIO 18 and keeps blinking until the first frame is
   drawn, then holds the LED on.

With network, the LED goes solid about 3 minutes after power-on. Without
network it takes 2 minutes longer, because the network-time wait has to time
out.

## Status LED (GPIO 18) behaviour

| LED                    | Meaning                                                  |
|------------------------|----------------------------------------------------------|
| On, steady (first ~20 s) | Powered; firmware/kernel starting                      |
| Blinking               | Booting, or clock starting up and drawing its first frame |
| On, steady             | Clock running; last refresh succeeded                    |
| Off                    | Clock stopped (exit, error, shutdown), or a refresh failed after 5 tries (comes back on at the next success) |

- If the LED **keeps blinking** long after boot, `epd_clock.py` never started
  or never claimed the pin: check `~/epd_clock.log` and
  `systemctl status epd-boot-blink`.
- After the clock exits the LED stays off; the boot blinker doesn't restart.
- If the process is killed with `kill -9`, the pin can stay where it was.

Check the level with `pinctrl get 18` (`hi`/`lo`) and the owner with
`gpioinfo -c gpiochip0 18` (`consumer="lg"` = epd_clock.py).

Boot blinker files: `/usr/local/sbin/epd-boot-blink` (the loop) and
`/etc/systemd/system/epd-boot-blink.service` (enabled, `WantedBy=sysinit.target`).
It uses `pinctrl`, which writes the GPIO registers without claiming the line,
and stops once `gpioinfo` shows a consumer on line 18.

## Timekeeping

- `epd_clock.py` asks `pool.ntp.org` for the time every 10 minutes and adds the
  difference to the system clock. If that fails it uses the system clock.
- The system clock is set from the DS3231 at boot, and `systemd-timesyncd`
  syncs it over the network when online. While synced, the kernel writes the
  time back to the RTC every ~11 minutes (`CONFIG_RTC_SYSTOHC`), so the RTC
  stays corrected.
- **The display runs 2 min 57 s fast on purpose:** `DISPLAY_OFFSET_SECONDS`
  in `epd_clock.py` (set to 0 to show the exact time). It only affects the
  display, not the system clock or the RTC.
- The RTC stores **UTC**. The Pi's own timezone is Europe/London; the display
  shows **America/Vancouver** (`TIMEZONE` in `epd_clock.py`, or `--tz=`).
- **Battery out while powered:** no effect. **Battery out and Pi unpowered:**
  the RTC loses the time. The next boot logs `rtc-ds1307 1-0068: SET TIME!`
  and `hctosys: unable to read the hardware clock`. Online, it fixes itself
  within minutes. Offline, the clock starts from the last time timesyncd saved
  before shutdown and is wrong until the network returns.
- DS3231 accuracy is about +/-2 ppm (at most about a minute a year).

## Configuration changes from a stock install

`/boot/firmware/config.txt` (backup of the pre-RTC version:
`config.txt.bak-pre-i2c`):

| Line                       | Why                                         |
|----------------------------|---------------------------------------------|
| `dtparam=spi=on`           | SPI0 for the display                        |
| `dtparam=i2c_arm=on`       | I2C bus 1 for the RTC                       |
| `dtoverlay=i2c-rtc,ds3231` | Load the DS3231 driver as `/dev/rtc0` (under `[all]`) |
| `gpio=18=op,dh`            | Status LED on from firmware start (under `[all]`) |

Also added: `epd-boot-blink.service` (see "Status LED" above).

Packages: `python3-gpiozero`, `python3-lgpio`, `python3-spidev`, `python3-pil`,
`i2c-tools`, `git`. The user `admin` is in the `gpio`, `spi` and `i2c` groups,
so nothing needs root.

The Waveshare library is a clone of https://github.com/waveshareteam/e-Paper
at commit `a794fbc` (2026-08-19), unmodified.

## Files in /home/admin/rpi

| File                  | What it is                                                   |
|-----------------------|--------------------------------------------------------------|
| `epd_clock.py`        | The clock (run at boot). `--once`, `--minutes`, `--quiet`, `--tz=` |
| `start_epd_clock.sh`  | Boot wrapper run by cron                                     |
| `~/epd_clock.log`     | Clock output (outside the repo). Not rotated, small with `--quiet` |
| `epd_text.py`         | Write a line of text to the display                          |
| `epd_dialogue.py`     | Show a screenplay's dialogue on the display                  |
| `excerpt.txt`         | Sample screenplay for `epd_dialogue.py`                      |
| `~/e-Paper/`          | Waveshare driver repo                                        |

## Things not obvious from the code

- **RPi.GPIO is stubbed out** in every script. The Waveshare driver imports it
  but does its GPIO through gpiozero/lgpio, and the installed RPi.GPIO
  (`python3-rpi-lgpio`) crashes on this B+'s revision code (`0010`).
- **Only one display script at a time.** The clock holds GPIO 17, 18, 24 and
  25 while running, so `epd_text.py` / `epd_dialogue.py` fail until it stops.
  Those two scripts also use GPIO 18 as the driver's PWR pin (high while they
  run).
- **Panel wear.** The clock does a partial refresh every second and a full one
  every 5 minutes. Waveshare recommends refreshing at most every ~3 minutes for
  long life; `--minutes` is gentler.
- **Occasional "panel BUSY stuck high"** in the log is handled: after 15 s the
  script resets the panel with a full refresh and retries (up to 5 times).
- No HDMI display is used. `vc4-kms-v3d` is still on from the stock config.

## Common commands

```sh
pgrep -af epd_clock.py                       # is the clock running?
tail -f ~/epd_clock.log                      # clock output
pkill -f epd_clock.py                        # stop it (LED goes off)
setsid nohup python3 ~/rpi/epd_clock.py --quiet >> ~/epd_clock.log 2>&1 &  # start it
pinctrl get 18                               # status LED level
systemctl status epd-boot-blink              # boot blinker (normally exited)
cat /sys/class/rtc/rtc0/date /sys/class/rtc/rtc0/time   # RTC time (UTC)
timedatectl                                  # system time, sync status
dmesg | grep rtc                             # RTC driver messages
sudo modprobe i2c-dev && sudo i2cdetect -y 1 # scan I2C (RTC shows as UU at 68)
```

## System files (`system/`)

Copies of files installed outside this repo, kept here so they are tracked.
They are copied as installed; reinstall with:

```sh
sudo install -m 755 system/epd-boot-blink /usr/local/sbin/
sudo install -m 644 system/epd-boot-blink.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable epd-boot-blink
sudo cp system/config.txt /boot/firmware/config.txt   # then reboot
```

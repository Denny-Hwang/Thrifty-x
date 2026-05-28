# Raspberry Pi 5 — Thrifty-X RX Site Installation Guide

This document summarizes the procedure for configuring a standalone RX node
with a Raspberry Pi 5 (64-bit Bookworm) + Airspy Mini/R2 combination.
Refer to the legacy `rpi/installation.md` (Pi 3 / Jessie / fastcard) only when
operating with RTL-SDR.

---

## 1. Recommended Hardware

| Item | Recommended Spec |
|---|---|
| Board | Raspberry Pi 5 (4 GB or more) |
| Power | Official 27W USB-C PD adapter (5V/5A) |
| Storage | microSD 32 GB (boot) + USB SSD 128 GB↑ (`/var/lib/thriftyx`) |
| Cooling | Official active cooler or case + fan (prevents throttling) |
| SDR | Airspy Mini or Airspy R2 |
| Cable | Short, high-quality USB 2.0/3.0 (external power required when using a USB hub) |
| Time sync | Internet (WAN) or local NTP/PTP server |

---

## 2. OS Preparation

```bash
# After installing Raspberry Pi OS 64-bit (Bookworm) Lite
sudo apt update && sudo apt full-upgrade -y
sudo raspi-config         # Hostname=rx0, Locale, Timezone, Expand FS
```

Add to `/boot/firmware/config.txt`:

```
# Allow 1.6 A output per USB port (when using the official 27W PD adapter)
usb_max_current_enable=1
```

Optional: Disable HDMI/Bluetooth to save power (headless node).

---

## 3. Package Installation

```bash
sudo apt install -y \
    python3 python3-venv python3-pip git \
    build-essential cmake pkg-config \
    libusb-1.0-0-dev \
    libfftw3-dev \
    airspy libairspy-dev \
    chrony

# (Optional) logging/monitoring
sudo apt install -y htop tmux rsync
```

> The Bookworm `airspy` package is libairspy 1.0.10 or higher and provides all of
> `airspy_open_sn`, `airspy_list_devices`, `airspy_get_samplerates`, and
> `airspy_set_packing` that the Thrifty-X HAL requires.

### 3.1 udev Rules (User Permissions)

The apt `airspy` package installs `/lib/udev/rules.d/60-libairspy0.rules`.
The user account must belong to the `plugdev` group:

```bash
sudo usermod -aG plugdev $USER
# After re-login
groups | grep plugdev
```

### 3.2 Time Synchronization (Core of TDOA)

```bash
sudo systemctl disable --now systemd-timesyncd
sudo systemctl enable --now chrony
chronyc tracking         # Check synchronization status
```

For sites without WAN, configure one of the adjacent nodes as a chrony server.

---

## 4. Thrifty-X Installation

```bash
git clone https://github.com/Denny-Hwang/thrifty-x.git ~/thrifty-x
cd ~/thrifty-x
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[analysis,fft]"   # fft = pyfftw, analysis = matplotlib
```

> **If pyfftw installation fails**: In environments where there is no pyfftw
> wheel in the Bookworm ARM64 index, or where `libfftw3l-dev` (long-double FFTW)
> is missing, the source build fails. In this case, there are two options.
>
> 1. **Install without pyfftw** — the capture loop automatically falls back to
>    NumPy FFT (`thriftyx/signal_utils.py`). Performance is about 1.3~1.7x
>    slower, but functionality is identical:
>    ```bash
>    pip install -e ".[analysis]"   # excluding the fft extra
>    ```
>
> 2. **Build with the bundled long-double removal patch applied** —
>    `rpi/pyFFTW-0.9.2-no-fftwl.patch` is for that purpose. Roughly:
>    ```bash
>    pip download --no-binary=:all: --no-deps pyfftw==0.13.* -d /tmp/pyfftw-src
>    cd /tmp/pyfftw-src && tar xzf pyFFTW-*.tar.gz && cd pyFFTW-*
>    patch -p1 < ~/thrifty-x/rpi/pyFFTW-0.9.2-no-fftwl.patch
>    pip install .
>    ```
>    After applying, verify with
>    `python -c "import pyfftw; print(pyfftw.__version__)"`.

### 4.1 Installation Verification

```bash
python -c "import thriftyx, numpy, scipy; print(thriftyx.__version__)"
python -c "from thriftyx.hal.airspy_mini import list_airspy_serials; print(list_airspy_serials())"
thriftyx --help
```

If the device list is empty, check the USB connection/permissions/`lsusb | grep Airspy`.

---

## 5. Data Directory

It is recommended to mount the USB SSD at `/var/lib/thriftyx` (to avoid SD card wear).

```bash
sudo mkdir -p /var/lib/thriftyx/{card,toad,log}
sudo chown -R $USER:$USER /var/lib/thriftyx
```

`/etc/fstab` example (check the UUID with `lsblk -f`):

```
UUID=XXXX-XXXX  /var/lib/thriftyx  ext4  defaults,noatime,nofail  0  2
```

---

## 6. Capture Configuration

Copy and use `rpi/thriftyx-capture.cfg.example`:

```bash
cp ~/thrifty-x/rpi/thriftyx-capture.cfg.example /var/lib/thriftyx/capture.cfg
$EDITOR /var/lib/thriftyx/capture.cfg   # adjust rxid, tuner_freq, gain, etc.
```

Manual test capture:

```bash
source ~/thrifty-x/.venv/bin/activate
thriftyx capture /var/lib/thriftyx/card/test.card \
    --config /var/lib/thriftyx/capture.cfg --duration 60
```

---

## 7. systemd Service Registration

```bash
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-capture@.service /etc/systemd/system/
sudo cp ~/thrifty-x/rpi/systemd/thriftyx-capture@.env.example /etc/default/thriftyx-capture@rx0
sudo $EDITOR /etc/default/thriftyx-capture@rx0     # adjust USER, paths
sudo systemctl daemon-reload
sudo systemctl enable --now thriftyx-capture@rx0.service
journalctl -u thriftyx-capture@rx0 -f
```

Verify automatic startup after reboot:

```bash
sudo reboot
# After booting
systemctl status thriftyx-capture@rx0
```

---

## 8. Disk Cleanup (cron)

```bash
sudo cp ~/thrifty-x/rpi/cleanup_old_captures.sh /usr/local/bin/
sudo chmod +x /usr/local/bin/cleanup_old_captures.sh
( crontab -l 2>/dev/null; echo "0 * * * * /usr/local/bin/cleanup_old_captures.sh" ) | crontab -
```

Default policy: delete files older than 7 days in `/var/lib/thriftyx/card/`,
delete files older than 30 days in `log/`. Adjustable via environment variables.

---

## 9. Validation Checklist

All items in `docs/rpi5_validation_checklist.md` must pass for the node to be
judged ready for field deployment. If even one required item fails, it is **No-Go**.

---

## 10. Troubleshooting

| Symptom | Candidate Cause | Action |
|---|---|---|
| `libairspy not available` | apt package not installed / wrong path | `sudo apt install airspy libairspy-dev`, `ldconfig -p \| grep airspy` |
| `airspy_open() failed: -2` | permissions/USB busy | check plugdev group, occupation by another process (`lsof | grep airspy`) |
| Frequent USB errors at 6/10 MSPS | power/cable/hub | 27W PD + short direct cable, enable `--packing` |
| Throttling after 30~60 seconds | heat | `vcgencmd get_throttled`, inspect active cooler |
| Timestamp mismatch between nodes | time sync | `chronyc tracking`, check NTP source |
| Service fails once right after boot | USB enumeration delay | automatic retry with `RestartSec=10` (default) |

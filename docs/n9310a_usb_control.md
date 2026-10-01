# N9310A USB control and automated CW bench

This guide covers the Agilent/Keysight **N9310A RF Signal Generator** used by
Thrifty-X bench experiments. The supported path is:

```text
N9310A rear USB Type-B
        -> Windows usbipd
        -> WSL2 Ubuntu
        -> libusb / PyUSB
        -> PyVISA-py
        -> N9310A SCPI
        -> Thrifty-X bench scripts
```

The code intentionally does **not** depend on the Linux `usbtmc` kernel
module. This matters on stock WSL2 kernels where the USB device can be
attached successfully but `/dev/usbtmc0` is absent.

The N9310A commands used here are documented in the Keysight N9310A User's
Guide / Quick Start Guide: `:FREQuency:CW`, `:AMPLitude:CW`,
`:RFOutput:STATe`, `:MOD:STATe`, sweep-state commands and
`:SYSTem:ERRor?`.

- User's Guide: <https://www.keysight.com/us/en/assets/9018-02136/user-manuals/9018-02136.pdf>
- Quick Start Guide: <https://www.keysight.com/zz/en/assets/9018-01586/quick-start-guides/9018-01586.pdf>

## 1. Cable and Windows enumeration

Use the **rear USB Type-B (square) connector** for PC remote control. Do not
use the front-panel USB host/storage connector for this connection.

With the generator powered on, open an **Administrator PowerShell**:

```powershell
usbipd list
```

The N9310A should appear as:

```text
0957:2018  N9310A RF Signal Generator
```

Bind once for the current Windows USB identity, then attach it to WSL:

```powershell
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
usbipd list
```

If WSL NAT networking prevents usbipd from reaching the host, the validated
bench configuration is mirrored networking:

```ini
# %UserProfile%\.wslconfig
[wsl2]
networkingMode=mirrored
```

After changing `.wslconfig`, run `wsl --shutdown` once from Windows.

## 2. WSL USB and permission check

```bash
cd ~/workspace/Thrifty-x
source .venv/bin/activate
lsusb -d 0957:2018
```

Expected identity:

```text
0957:2018 Agilent Technologies, Inc. N9310A RF Signal Generator
```

The interface should report USB Test and Measurement class/subclass:

```bash
lsusb -v -d 0957:2018 2>/dev/null | \
  grep -E 'bInterfaceClass|bInterfaceSubClass|bInterfaceProtocol'
```

Expected values are class `254` (`0xfe`), subclass `3`, protocol `1`.

Install the repository udev rules with:

```bash
scripts/bench/wsl_setup.sh .venv
```

or create only the N9310A rule manually:

```bash
sudo tee /etc/udev/rules.d/99-n9310a.rules >/dev/null <<'EOF'
SUBSYSTEM=="usb", ATTR{idVendor}=="0957", ATTR{idProduct}=="2018", MODE="0666"
EOF
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## 3. Python dependencies

N9310A support is optional:

```bash
python -m pip install -e ".[instrument]"
```

A full developer install also includes it:

```bash
python -m pip install -e ".[all]"
```

The stack is `PyVISA -> PyVISA-py -> PyUSB/libusb`. `psutil` and
`zeroconf` are not required for USB control.

## 4. Connection verification

```bash
python scripts/n9310a_control.py discover
python scripts/n9310a_control.py status
```

A typical VISA resource is:

```text
USB0::2391::8216::<serial>::0::INSTR
```

`2391` and `8216` are decimal forms of USB VID/PID `0957:2018`.

## 5. Controlled set/readback test

Set 161.315 MHz and -100 dBm while leaving RF **OFF**:

```bash
python scripts/n9310a_control.py set --frequency 161.315M --power -100
```

Enable RF only when the RF chain is safe:

```bash
python scripts/n9310a_control.py set \
  --frequency 161.315M --power -100 --rf-on
```

Turn RF off:

```bash
python scripts/n9310a_control.py off
```

Every set operation reads frequency, power and RF state back and checks the
SCPI error queue. Frequency/power changes are made with RF forced off first.

## 6. Automated RTL-SDR / Airspy CW bench

The normal operator workflow is now:

1. Attach N9310A to WSL and verify `status` once.
2. Review the experiment plan printed by the script.
3. Connect the requested receiver when prompted.
4. Do not touch generator level/frequency; the script sweeps them.
5. At the receiver-swap prompt, move **only** the receiver end of the fixed RF
   chain.
6. Review `results.csv`, `report.md`, and `cw_levels.png`.

Example:

```bash
RUN=run1 \
UNITS="RTL R2-A" \
TONE=161.315M \
LEVELS='off,-110:-60:5' \
R2_RATES='2.5M 10M' \
R2_STAGES='0/0/8' \
AMP_GAIN=20 LOSS=0 \
scripts/bench/run_cw_bench.sh
```

`GENERATOR_MODE=auto` is the default. Set `GENERATOR_MODE=manual` to keep
the old front-panel workflow.

The automatic layer keeps one receiver open for the full level sweep at a
given sample rate and always attempts to return the N9310A to RF OFF when the
sweep exits, including exceptions and Ctrl-C.

## 7. Safety

- RF is forced OFF before generator frequency/power changes and receiver swaps.
- The CW benchmark refuses planned receiver input above its configured safety
  limit unless explicitly forced; keep amplifier gain/loss metadata correct.
- Do not hot-swap the receiver while RF is on.
- Keep the same amplifier, attenuator, cable, antenna/termination and placement
  between receiver conditions.
- If more than one N9310A is attached, auto-discovery refuses to guess; pass
  the complete VISA resource through `N9310A_RESOURCE`.

## 8. Troubleshooting

| Symptom | Check |
|---|---|
| `usbipd list` has no N9310A | rear USB Type-B cable, power, Windows enumeration |
| `lsusb` has no `0957:2018` | `usbipd bind/attach`, WSL distribution/networking |
| PyVISA finds it only under `sudo` | udev/device-node write permission |
| `/dev/usbtmc0` is absent | acceptable on WSL; use PyVISA-py/PyUSB path |
| `No N9310A found` | `lsusb`, permissions, then `n9310a_control.py discover` |
| SCPI readback/error failure | stop sweep, leave RF off, run `status` |

#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# One-time Ubuntu/WSL2 setup for the RTL-SDR / Airspy / N9310A bench.
# Usage: scripts/bench/wsl_setup.sh [VENV_DIR]
# Default: repository-local .venv
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENV=${1:-${REPO}/.venv}

echo "== packages"
sudo apt-get update
sudo apt-get install -y rtl-sdr libairspy0 airspy usbutils \
    python3-venv python3-dev build-essential git

echo "== udev rules"
echo "   RTL2832U 0bda:2838, Airspy 1d50:60a1, N9310A 0957:2018"
sudo tee /etc/udev/rules.d/60-thriftyx-bench.rules >/dev/null <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60a1", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0957", ATTRS{idProduct}=="2018", MODE="0666"
RULES
if command -v udevadm >/dev/null && sudo udevadm control --reload-rules 2>/dev/null; then
    sudo udevadm trigger || true
else
    echo "   udev is not running: after usbipd attach, grant access to the"
    echo "   attached device node (see docs/n9310a_usb_control.md)."
fi

echo "== Python venv at ${VENV}"
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
. "${VENV}/bin/activate"
python -m pip install -q --upgrade pip setuptools wheel
python -m pip install -q -e "${REPO}[analysis,dev,instrument]"

echo "== self-test (no hardware)"
(cd "${REPO}" && python -m pytest -q \
    tests/unit/test_bench_cw_level.py \
    tests/unit/test_r820t_register_model.py \
    tests/unit/test_n9310a.py \
    tests/unit/test_bench_cw_auto.py)

echo
echo "librtlsdr in use: $(ldconfig -p 2>/dev/null | grep -m1 'librtlsdr.so' | awk '{print $NF}')"
echo "Done. Activate with: source ${VENV}/bin/activate"
echo "Then attach hardware from Windows and verify:"
echo "  lsusb | grep -Ei '0bda:2838|1d50:60a1|0957:2018'"
echo "  python scripts/n9310a_control.py status"

#!/usr/bin/env bash
# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only
#
# One-time laptop setup (Ubuntu on WSL2, or native Ubuntu) for the
# RTL-SDR vs Airspy CW bench (docs/bench_rtl_vs_r2_cw.md):
#   - rtl-sdr tools (osmocom librtlsdr) and libairspy + airspy tools
#   - udev rules so both receivers open without sudo
#   - a Python venv with thriftyx installed from this checkout
#
# Usage:  scripts/bench/wsl_setup.sh [VENV_DIR]     (default ~/thriftyx-venv)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
VENV=${1:-$HOME/thriftyx-venv}

echo "== packages"
sudo apt-get update
sudo apt-get install -y rtl-sdr libairspy0 airspy usbutils \
    python3-venv python3-dev build-essential git

echo "== udev rules (RTL2832U 0bda:2838, Airspy 1d50:60a1)"
sudo tee /etc/udev/rules.d/60-thriftyx-bench.rules >/dev/null <<'RULES'
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60a1", MODE="0666"
RULES
if command -v udevadm >/dev/null && sudo udevadm control --reload-rules 2>/dev/null; then
    sudo udevadm trigger || true
else
    echo "   udev is not running (WSL without systemd): after each"
    echo "   'usbipd attach' run:  sudo chmod 666 /dev/bus/usb/*/*"
fi

echo "== Python venv at ${VENV}"
python3 -m venv "${VENV}"
# shellcheck disable=SC1091
. "${VENV}/bin/activate"
pip install -q --upgrade pip
pip install -q -e "${REPO}[analysis,dev]"

echo "== self-test (no hardware)"
(cd "${REPO}" && python -m pytest -q tests/unit/test_bench_cw_level.py \
    tests/unit/test_r820t_register_model.py)

echo
echo "librtlsdr in use: $(ldconfig -p | grep -m1 'librtlsdr.so' | awk '{print $NF}')"
echo "Done.  Activate with:  . ${VENV}/bin/activate"
echo "Then attach the receivers from Windows (usbipd) and run:"
echo "  lsusb | grep -Ei '0bda:2838|1d50:60a1'; rtl_test -t; airspy_info"

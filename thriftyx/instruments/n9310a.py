"""Agilent/Keysight N9310A RF signal-generator control over USBTMC.

The N9310A enumerates as USB VID:PID 0957:2018.  This module deliberately
uses PyVISA-py/PyUSB in userspace so it also works on stock WSL2 kernels
that expose the USB device through usbipd but do not provide ``usbtmc.ko``.

PyVISA is an optional dependency.  Importing this module does not require
it; opening a generator does.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

N9310A_USB_VID = 0x0957
N9310A_USB_PID = 0x2018
_RESOURCE_TOKEN = f"::{N9310A_USB_VID}::{N9310A_USB_PID}::"


class N9310AError(RuntimeError):
    """Raised when discovery, SCPI I/O, or readback verification fails."""


@dataclass(frozen=True)
class N9310AStatus:
    resource: str
    idn: str
    frequency_hz: float
    power_dbm: float
    rf_on: bool


def _new_resource_manager() -> Any:
    try:
        import pyvisa
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise N9310AError(
            "PyVISA is not installed. Install Thrifty-X with "
            "'pip install -e \".[instrument]\"'."
        ) from exc
    return pyvisa.ResourceManager("@py")


def discover_n9310a(resource_manager: Any | None = None) -> str:
    """Return the sole connected N9310A VISA resource.

    Restrict discovery to USB INSTR resources so PyVISA-py does not also
    probe TCP/IP backends (and therefore does not require psutil/zeroconf).
    """
    owned = resource_manager is None
    rm = resource_manager or _new_resource_manager()
    try:
        resources = tuple(rm.list_resources("USB?*::INSTR"))
        matches = [r for r in resources if _RESOURCE_TOKEN in r]
        if not matches:
            raise N9310AError(
                "No N9310A found (expected USB VID:PID 0957:2018). "
                "Check usbipd attach and Linux USB permissions."
            )
        if len(matches) > 1:
            raise N9310AError(
                "Multiple N9310A resources found; pass --resource explicitly: "
                + ", ".join(matches)
            )
        return matches[0]
    finally:
        if owned:
            rm.close()


class N9310A:
    """Small, readback-verified N9310A SCPI controller."""

    def __init__(self, resource: str = "auto", timeout_ms: int = 5000,
                 resource_manager: Any | None = None):
        self._owned_rm = resource_manager is None
        self.rm = resource_manager or _new_resource_manager()
        self.resource = (discover_n9310a(self.rm)
                         if resource.lower() == "auto" else resource)
        self.instrument = self.rm.open_resource(self.resource)
        self.instrument.timeout = int(timeout_ms)
        try:
            self.idn = self.instrument.query("*IDN?").strip()
            if "N9310A" not in self.idn.upper():
                raise N9310AError(
                    f"Resource {self.resource} is not an N9310A: {self.idn!r}")
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> "N9310A":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def close(self) -> None:
        inst = getattr(self, "instrument", None)
        if inst is not None:
            try:
                inst.close()
            finally:
                self.instrument = None
        if getattr(self, "_owned_rm", False):
            rm = getattr(self, "rm", None)
            if rm is not None:
                rm.close()
                self.rm = None

    def _write(self, command: str) -> None:
        self.instrument.write(command)

    def _query(self, command: str) -> str:
        return self.instrument.query(command).strip()

    @staticmethod
    def _error_is_clear(text: str) -> bool:
        code = text.split(",", 1)[0].strip()
        try:
            return int(code) == 0
        except ValueError:
            return False

    def system_error(self) -> str:
        return self._query(":SYSTem:ERRor?")

    def assert_no_error(self, context: str = "SCPI command") -> None:
        first = self.system_error()
        if self._error_is_clear(first):
            return
        errors = [first]
        for _ in range(15):
            item = self.system_error()
            errors.append(item)
            if self._error_is_clear(item):
                break
        raise N9310AError(f"{context} failed: " + " | ".join(errors))

    def clear_status(self) -> None:
        self._write("*CLS")

    def frequency_hz(self) -> float:
        return float(self._query(":FREQuency:CW?"))

    def power_dbm(self) -> float:
        return float(self._query(":AMPLitude:CW?"))

    def rf_output(self) -> bool:
        return bool(int(float(self._query(":RFOutput:STATe?"))))

    def status(self) -> N9310AStatus:
        return N9310AStatus(
            resource=self.resource,
            idn=self.idn,
            frequency_hz=self.frequency_hz(),
            power_dbm=self.power_dbm(),
            rf_on=self.rf_output(),
        )

    def set_rf_output(self, enabled: bool, verify: bool = True) -> bool:
        self._write(f":RFOutput:STATe {'ON' if enabled else 'OFF'}")
        actual = self.rf_output() if verify else enabled
        if verify and actual != enabled:
            raise N9310AError(
                f"RF output readback mismatch: requested {enabled}, got {actual}")
        self.assert_no_error("RF output state")
        return actual

    def configure_cw(self, frequency_hz: float, power_dbm: float,
                     rf_on: bool = False) -> N9310AStatus:
        """Configure a plain CW tone, with RF forced OFF while changing it."""
        if not math.isfinite(frequency_hz) or frequency_hz <= 0:
            raise ValueError("frequency_hz must be positive and finite")
        if not math.isfinite(power_dbm):
            raise ValueError("power_dbm must be finite")

        self.clear_status()
        self._write(":RFOutput:STATe OFF")
        self._write(":MOD:STATe OFF")
        self._write(":SWEep:RF:STATe OFF")
        self._write(":SWEep:LF:STATe OFF")
        self._write(":SWEep:AMPLitude:STATe OFF")
        self._write(f":FREQuency:CW {frequency_hz:.12g} Hz")
        self._write(f":AMPLitude:CW {power_dbm:.12g} dBm")

        actual_f = self.frequency_hz()
        actual_p = self.power_dbm()
        if not math.isclose(actual_f, frequency_hz, rel_tol=0.0, abs_tol=1.0):
            raise N9310AError(
                f"frequency readback mismatch: requested {frequency_hz} Hz, "
                f"got {actual_f} Hz")
        if not math.isclose(actual_p, power_dbm, rel_tol=0.0, abs_tol=0.01):
            raise N9310AError(
                f"power readback mismatch: requested {power_dbm} dBm, "
                f"got {actual_p} dBm")
        self.assert_no_error("CW configuration")

        if rf_on:
            self.set_rf_output(True)
        return self.status()

    def prepare_level(self, frequency_hz: float,
                      power_dbm: float | None) -> N9310AStatus:
        """Prepare one sweep level; ``None`` means RF OFF/noise reference."""
        if power_dbm is None:
            self.set_rf_output(False)
            return self.status()
        return self.configure_cw(frequency_hz, power_dbm, rf_on=True)

# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# Based on Thrifty by Schalk Willem Krüger
# (https://github.com/swkrueger/Thrifty)
#
# This file is part of Thrifty-X.
#
# SPDX-License-Identifier: GPL-3.0-only

"""Thrifty-X exception hierarchy."""


class ThriftyXError(Exception):
    """Base exception for all Thrifty-X errors."""


class DeviceError(ThriftyXError):
    """Hardware device errors."""


class DeviceNotFoundError(DeviceError):
    """SDR device not found or not connected."""


class DeviceConfigError(DeviceError):
    """Invalid device configuration."""


class DeviceCaptureError(DeviceError):
    """Error during sample capture."""


class ConfigError(ThriftyXError):
    """Configuration errors."""


class ConfigSyntaxError(ConfigError):
    """Malformed configuration file."""
    def __init__(self, line_no, msg):
        self.line_no = line_no
        self.msg = msg
        super().__init__("line #%d: %s" % (line_no, msg))

    def __str__(self):
        return "line #%d: %s" % (self.line_no, self.msg)


class SettingKeyError(ConfigError):
    """Unknown setting key."""
    def __init__(self, msg):
        self.msg = msg
        super().__init__(msg)

    def __str__(self):
        return repr(self.msg)


class ConfigValidationError(ConfigError):
    """Cross-field validation failure."""


class DetectionError(ThriftyXError):
    """Signal detection errors."""


class TemplateError(DetectionError):
    """Template generation or loading error."""


class EstimationError(ThriftyXError):
    """Position or TDOA estimation errors."""


class FileFormatError(ThriftyXError):
    """Invalid or unrecognized file format."""

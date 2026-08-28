# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the device-derived default sample rate (review finding N6).

The DEFINITIONS default (2.4M) is RTL-SDR-only; with the default
``--device-type airspy_mini`` a bare ``thriftyx capture out.card``
previously failed validation out of the box.
"""

import argparse

from thriftyx import settings
from thriftyx.airspy_capture import _apply_device_default_rate
from thriftyx.settings import Namespace


def _cfg(device_type, sample_rate, explicit=frozenset(), **extra):
    ns = Namespace({'device_type': device_type, 'sample_rate': sample_rate,
                    **extra})
    ns.explicit_keys = frozenset(explicit)
    return ns


class TestApplyDeviceDefaultRate:
    def test_mini_gets_3m_from_stock_default(self):
        cfg = _apply_device_default_rate(_cfg('airspy_mini', 2.4e6))
        assert int(cfg.sample_rate) == 3_000_000
        assert int(cfg['sample_rate']) == 3_000_000  # dict view too

    def test_r2_gets_2_5m_from_stock_default(self):
        cfg = _apply_device_default_rate(_cfg('airspy_r2', 2.4e6))
        assert int(cfg.sample_rate) == 2_500_000

    def test_rtlsdr_keeps_stock_default(self):
        cfg = _apply_device_default_rate(_cfg('rtlsdr', 2.4e6))
        assert int(cfg.sample_rate) == 2_400_000

    def test_explicit_value_is_never_touched(self):
        # Even the (invalid) stock rate is preserved when explicit — the
        # validator owns that error message.
        cfg = _apply_device_default_rate(
            _cfg('airspy_mini', 2.4e6, explicit={'sample_rate'}))
        assert int(cfg.sample_rate) == 2_400_000

    def test_non_default_programmatic_value_is_never_touched(self):
        # A caller that bypasses load_args (no explicit_keys) but supplies
        # a non-stock rate is treated as intentional.
        cfg = _apply_device_default_rate(_cfg('airspy_mini', 6e6))
        assert int(cfg.sample_rate) == 6_000_000

    def test_block_params_readjusted_for_new_rate(self):
        # At 3 Msps the template is ~3069 samples; a 2.4M-era history of
        # 2000 would be too small and must be enlarged.
        cfg = _apply_device_default_rate(
            _cfg('airspy_mini', 2.4e6, block_history=2000, block_size=8192))
        assert cfg['block_history'] >= 3068
        assert cfg['block_size'] >= cfg['block_history'] * 2

    def test_validator_accepts_the_derived_default(self):
        from thriftyx import config_validator
        cfg = _apply_device_default_rate(
            _cfg('airspy_mini', 2.4e6, tuner_freq=433.83e6))
        config_validator.validate_config(cfg)  # must not raise


class TestExplicitKeysPlumbing:
    def test_cli_flag_is_explicit(self):
        parser = argparse.ArgumentParser()
        cfg, _ = settings.load_args(parser, ['sample_rate', 'device_type'],
                                    argv=['-s', '6M'])
        assert 'sample_rate' in cfg.explicit_keys
        assert 'device_type' not in cfg.explicit_keys

    def test_default_is_not_explicit(self):
        parser = argparse.ArgumentParser()
        cfg, _ = settings.load_args(parser, ['sample_rate'], argv=[])
        assert 'sample_rate' not in cfg.explicit_keys

    def test_config_file_is_explicit(self, tmp_path, monkeypatch):
        cfgfile = tmp_path / 'detector.cfg'
        cfgfile.write_text('sample_rate: 6M\n')
        parser = argparse.ArgumentParser()
        cfg, _ = settings.load_args(parser, ['sample_rate'],
                                    argv=['-c', str(cfgfile)])
        assert 'sample_rate' in cfg.explicit_keys
        assert int(cfg.sample_rate) == 6_000_000

    def test_explicit_keys_not_in_dict_iteration(self):
        parser = argparse.ArgumentParser()
        cfg, _ = settings.load_args(parser, ['sample_rate'], argv=[])
        assert 'explicit_keys' not in dict(cfg)


class TestApplyDeviceDefaultBitDepth:
    """Dedicated tests for _apply_device_default_bit_depth (previously
    verified only empirically)."""

    @staticmethod
    def _bd_cfg(device_type, bit_depth, explicit=frozenset()):
        from thriftyx.airspy_capture import _apply_device_default_bit_depth
        ns = Namespace({'device_type': device_type, 'bit_depth': bit_depth})
        ns.explicit_keys = frozenset(explicit)
        return _apply_device_default_bit_depth(ns)

    def test_airspy_gets_12_from_stock_default(self):
        cfg = self._bd_cfg('airspy_mini', 8)
        assert cfg['bit_depth'] == 12

    def test_r2_gets_12_from_stock_default(self):
        cfg = self._bd_cfg('airspy_r2', 8)
        assert cfg['bit_depth'] == 12

    def test_rtlsdr_keeps_8(self):
        cfg = self._bd_cfg('rtlsdr', 8)
        assert cfg['bit_depth'] == 8

    def test_explicit_value_left_for_validator(self):
        cfg = self._bd_cfg('airspy_mini', 8, explicit={'bit_depth'})
        assert cfg['bit_depth'] == 8

    def test_explicit_keys_survive(self):
        from thriftyx.airspy_capture import _apply_device_default_bit_depth
        ns = Namespace({'device_type': 'airspy_mini', 'bit_depth': 8})
        ns.explicit_keys = frozenset({'tuner_freq'})
        out = _apply_device_default_bit_depth(ns)
        assert out.explicit_keys == frozenset({'tuner_freq'})

    def test_stock_airspy_config_validates_without_warnings(self):
        from thriftyx import config_validator
        from thriftyx.airspy_capture import (_apply_device_default_bit_depth,
                                             _apply_device_default_rate)
        cfg = _cfg('airspy_mini', 2.4e6, tuner_freq=433.83e6, bit_depth=8)
        cfg = _apply_device_default_rate(cfg)
        cfg = _apply_device_default_bit_depth(cfg)
        warnings = config_validator.validate_config(cfg)
        assert warnings == []

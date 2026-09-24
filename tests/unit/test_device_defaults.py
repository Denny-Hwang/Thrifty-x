# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Device-derived setting defaults.

``sample_rate`` and ``bit_depth`` have no fixed default: settings.load()
takes them from the profile of the configured ``device_type``
(thriftyx.hal.profiles), so a bare ``thriftyx capture out.card`` with
the default Airspy Mini validates cleanly, and ``tdoa`` falls back to
the same rate capture used.
"""

import argparse

import pytest

from thriftyx import config_validator, settings, tdoa_est
from thriftyx.exceptions import ConfigValidationError
from thriftyx.hal import profiles


class TestDerivedDefaults:
    @pytest.mark.parametrize("device_type,rate,bit_depth", [
        ('airspy_mini', 6_000_000, 12),
        ('airspy_r2', 10_000_000, 12),
        ('rtlsdr', 2_400_000, 8),
    ])
    def test_defaults_follow_device_type(self, device_type, rate, bit_depth):
        values = settings.load({'device_type': device_type})
        assert values['sample_rate'] == rate
        assert values['bit_depth'] == bit_depth

    def test_default_device_is_airspy_mini(self):
        values = settings.load()
        assert values['device_type'] == profiles.DEFAULT_DEVICE_TYPE
        assert values['sample_rate'] == \
            profiles.AIRSPY_MINI.default_sample_rate

    def test_explicit_values_are_kept(self):
        values = settings.load({'device_type': 'airspy_mini',
                                'sample_rate': '3M', 'bit_depth': '8'})
        assert values['sample_rate'] == 3e6
        assert values['bit_depth'] == 8

    def test_block_params_adjust_for_the_derived_rate(self):
        values = settings.load({'device_type': 'airspy_r2',
                                'sample_rate': '10M'})
        assert (values['block_size'], values['block_history']) == \
            (65536, 20539)

    def test_unknown_device_type_is_a_config_error(self):
        with pytest.raises(ConfigValidationError, match="hackrf"):
            settings.load({'device_type': 'hackrf'})

    def test_help_lists_per_device_defaults(self):
        parser = argparse.ArgumentParser()
        settings.add_argparse_arguments(parser, ['sample_rate'])
        assert "airspy_r2 10M" in parser.format_help()

    @pytest.mark.parametrize("device_type", list(profiles.PROFILES))
    def test_stock_config_validates_without_warnings(self, device_type):
        values = settings.load({'device_type': device_type})
        assert config_validator.validate_config(values) == []


class TestTdoaRateFallback:
    def test_cli_rate_wins(self):
        assert tdoa_est._resolve_sample_rate(6e6, None) == 6e6

    def test_config_rate(self, tmp_path):
        cfg = tmp_path / 'detector.cfg'
        cfg.write_text('sample_rate: 10M\n')
        assert tdoa_est._resolve_sample_rate(None, str(cfg)) == 10e6

    def test_device_default_matches_capture(self, tmp_path, caplog):
        cfg = tmp_path / 'detector.cfg'
        cfg.write_text('device_type: airspy_r2\n')
        rate = tdoa_est._resolve_sample_rate(None, str(cfg))
        assert rate == settings.load({'device_type': 'airspy_r2'})[
            'sample_rate']
        assert "using the airspy_r2 default" in caplog.text


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

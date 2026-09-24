"""
Unit test for settings module.
"""

import argparse
import io

import pytest

from thriftyx import settings
from thriftyx.exceptions import ConfigValidationError

DEFAULT_FOO = '2e6'
DEFAULT_BAZ = '1e6'

DEFINITIONS = {
    'foo': settings.Definition(
        args=['--foo', '-f'],
        parser=float,
        default=DEFAULT_FOO,
        description=None,
    ),

    'bar.baz': settings.Definition(
        args=['--baz', '-b'],
        parser=float,
        default=DEFAULT_BAZ,
        description=None,
    ),

    'xyzzy': settings.Definition(
        args=['--xyzzy', '-x'],
        parser=str,
        default=None,
        description=None,
    ),
}


def test_argparse_simple():
    """Can generate argparse arguments."""
    parser = argparse.ArgumentParser()
    settings.add_argparse_arguments(parser, ['foo', 'bar.baz'],
                                    definitions=DEFINITIONS)
    args = vars(parser.parse_args(['-f', '12.34', '--baz=56.78']))
    assert args['foo'] == '12.34'
    assert args['bar.baz'] == '56.78'


def test_load_default_values():
    """Can load default values."""
    values = settings.load(None, None, DEFINITIONS)
    assert len(values) == 2
    assert values['foo'] == float(DEFAULT_FOO)
    assert values['bar.baz'] == float(DEFAULT_BAZ)


def test_load_config():
    """Can load settings from config file."""
    config = io.StringIO("bar.baz:   1234.56")
    values = settings.load(None, config, DEFINITIONS)
    assert values['foo'] == float(DEFAULT_FOO)
    assert values['bar.baz'] == 1234.56


def test_load_syntax_error():
    """Throw ConfigSyntaxError if config's syntax is invalid."""
    config = io.StringIO("foobar")
    with pytest.raises(settings.ConfigSyntaxError):
        settings.load(None, config, DEFINITIONS)


def test_load_key_error_config():
    """Throw SettingKeyError if a setting in the config file is not defined."""
    config = io.StringIO("foobar: 1")
    with pytest.raises(settings.SettingKeyError):
        settings.load(None, config, DEFINITIONS)


def test_load_key_error_arg():
    """Throw SettingKeyError if arg contains a key without a definition."""
    args = {'foobar': '1'}
    with pytest.raises(settings.SettingKeyError):
        settings.load(args, None, DEFINITIONS)


def test_load_args():
    """Can args override config and default."""
    config = io.StringIO("bar.baz:   12.34")
    args = {'bar.baz': "7.8", 'foo': '9.0'}
    values = settings.load(args, config, DEFINITIONS)
    assert values['foo'] == 9.0
    assert values['bar.baz'] == 7.8


def test_loadargs(tmpdir):
    """End-to-end test for load_args function."""
    tmp = tmpdir.join("thrift.cfg")
    tmp.write("xyzzy: xyz\nfoo: 1.2\nbar.baz: 3.6")

    parser = argparse.ArgumentParser()
    parser.add_argument('-a', dest='a')
    argv = ['-a', 'extra', '--foo=2.3', '-c', tmp.strpath]

    config, args = settings.load_args(parser, ['xyzzy', 'foo'],
                                      argv=argv, definitions=DEFINITIONS)
    args.pop('verbose')
    assert len(config) == 2
    assert len(args) == 1
    assert config['xyzzy'] == 'xyz'
    assert config['foo'] == 2.3
    assert args['a'] == 'extra'


class TestAutoAdjustBlockParams:
    """Auto-adjust must only rewrite default-derived block parameters.

    Regression tests for the M4 finding: explicitly-configured
    block_size / block_history were silently replaced based on a
    hardcoded 1023-chip template estimate.
    """

    def test_defaults_untouched_at_rtlsdr_rate(self):
        values = settings.load({'device_type': 'rtlsdr'}, None)
        assert values['block_size'] == 16384
        assert values['block_history'] == 4920

    def test_default_device_gets_6msps_block_params(self):
        # The default device (Airspy Mini) defaults to 6 MSPS; the overlap
        # holds an 11-bit (2047-chip) template: 12285 samples + 64.
        values = settings.load(None, None)
        assert (values['block_size'], values['block_history']) == \
            (32768, 12349)

    @pytest.mark.parametrize('rate, geometry', [
        ('2.4M', (16384, 4920)), ('2.5M', (16384, 5182)),
        ('3M', (16384, 6206)), ('6M', (32768, 12349)),
        ('10M', (65536, 20539))])
    def test_defaults_hold_an_eleven_bit_template(self, rate, geometry):
        """Upstream Thrifty transmitters send an 11-bit code (the captured
        example/template.npy is gold(11, 0)); the defaults used to fit
        only 1023-chip templates, so those transmitters could not be
        correlated at the Airspy rates."""
        from thriftyx.template_generate import generate
        values = settings.load({'sample_rate': rate}, None)
        assert (values['block_size'], values['block_history']) == geometry
        sps = values['sample_rate'] / values['chip_rate']
        template = generate(11, 0, sps)
        assert values['block_history'] >= len(template) - 1

    def test_explicit_ten_bit_history_warns(self, caplog):
        """A config pinned to the old 10-bit overlap is kept, but its
        captures can never be correlated with an 11-bit template -- a
        warning, not an INFO line, even if the fleet sends 10-bit codes
        today."""
        import logging as _logging
        with caplog.at_level(_logging.INFO):
            values = settings.load({'sample_rate': '6M',
                                    'block_history': '12278'}, None)
        assert values['block_history'] == 12278
        notes = [r for r in caplog.records if 'holds codes up to 10 bits'
                 in r.message]
        assert notes and all(r.levelno == _logging.WARNING for r in notes)

    def test_defaults_adjusted_at_6msps(self, caplog):
        import logging as _logging
        with caplog.at_level(_logging.INFO):
            values = settings.load({'sample_rate': '6M'}, None)
        # 6 Msps: template ~6140 > default history 4920 -> both adjusted.
        assert values['block_history'] > 4920
        assert values['block_size'] >= 2 * values['block_history']
        adjusted = [r for r in caplog.records if 'Auto-adjusted' in r.message]
        assert adjusted
        # Deriving defaults is routine: it must not warn on every run.
        assert all(r.levelno == _logging.INFO for r in adjusted)

    def test_explicit_history_kept_with_warning(self, caplog):
        import logging as _logging
        args = {'sample_rate': '6M', 'block_history': '4920'}
        with caplog.at_level(_logging.WARNING):
            values = settings.load(args, None)
        assert values['block_history'] == 4920
        assert any('block_history' in r.message and 'keeping' in r.message
                   for r in caplog.records)

    def test_explicit_block_size_kept_with_warning(self, caplog):
        import logging as _logging
        args = {'sample_rate': '6M', 'block_size': '16384'}
        with caplog.at_level(_logging.WARNING):
            values = settings.load(args, None)
        assert values['block_size'] == 16384
        assert any('block_size' in r.message and 'keeping' in r.message
                   for r in caplog.records)

    def test_explicit_via_config_file_kept(self):
        config = io.StringIO("sample_rate: 6M\nblock_history: 4920\n"
                             "block_size: 16384\n")
        values = settings.load(None, config)
        assert values['block_history'] == 4920
        assert values['block_size'] == 16384


@pytest.mark.parametrize('chip_rate', ['0', '-1M', '0.999707m', '999.707',
                                       '7M'])
def test_impossible_chip_rate_is_a_config_error(chip_rate):
    """0 used to raise ZeroDivisionError in every command; a lowercase
    'm' (milli) sized blocks at 2**45 samples, which passed validation
    and failed allocating 44.7 TiB."""
    config = io.StringIO("sample_rate: 6M\nchip_rate: {}\n".format(chip_rate))
    with pytest.raises(ConfigValidationError, match='chip_rate'):
        settings.load(None, config)


def test_chip_rate_is_checked_against_the_card_sample_rate():
    """A device-default rate is not final: a card replaces it, so the
    samples-per-chip range is checked against the recorded rate."""
    config = settings.Namespace(settings.load({'chip_rate': '30k'}, None))
    config.explicit_keys = frozenset({'chip_rate'})
    assert config.sample_rate == 6e6            # 200 samples/chip
    card = settings.apply_card_header(config, {'sample_rate': '2400000'})
    assert card.sample_rate == 2.4e6            # 80 samples/chip
    with pytest.raises(ConfigValidationError, match='samples per chip'):
        settings.apply_card_header(config, {'sample_rate': '10000000'})


def test_chip_rate_is_checked_against_the_device_sample_rate():
    config = io.StringIO("device_type: airspy_r2\nchip_rate: 0.999707M\n")
    values = settings.load(None, config)
    assert values['sample_rate'] == 10e6
    config = io.StringIO("device_type: airspy_r2\nchip_rate: 11M\n")
    with pytest.raises(ConfigValidationError, match='chip_rate'):
        settings.load(None, config)

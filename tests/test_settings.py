"""
Unit test for settings module.
"""

import argparse
import io

import pytest

from thriftyx import settings

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

    def test_defaults_untouched_at_stock_rate(self):
        values = settings.load(None, None)
        assert values['block_size'] == 16384
        assert values['block_history'] == 4920

    def test_defaults_adjusted_at_6msps(self, caplog):
        import logging as _logging
        with caplog.at_level(_logging.WARNING):
            values = settings.load({'sample_rate': '6M'}, None)
        # 6 Msps: template ~6140 > default history 4920 -> both adjusted.
        assert values['block_history'] > 4920
        assert values['block_size'] >= 2 * values['block_history']
        assert any('Auto-adjusted' in r.message for r in caplog.records)

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

# Copyright (C) 2025-2026 Sungjoo Hwang, PNNL
# SPDX-License-Identifier: GPL-3.0-only

"""Tests for the unified Qt detect-analysis viewer plumbing.

These cover the parts that do not require an X server:

* ``_try_qt_modules`` returns ``None`` when no Qt binding is importable.
* ``show_detections`` calls ``_show_detections_pyplot`` (not the Qt path)
  when ``prefer_qt=False`` or when no Qt stack is available.
* ``show_detections`` short-circuits on an empty detection list.
"""

import sys
import types

import pytest

matplotlib = pytest.importorskip("matplotlib")

from thriftyx import detect_analysis as da


def test_try_qt_modules_returns_none_when_no_binding(monkeypatch):
    """Probe must return None if neither PyQt5 nor PySide6 is importable."""
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name.startswith(("PyQt5", "PySide6")):
            raise ImportError("synthetic: " + name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    # Drop any cached Qt modules so importlib re-probes.
    for cached in list(sys.modules):
        if cached.startswith(("PyQt5", "PySide6")):
            monkeypatch.delitem(sys.modules, cached, raising=False)

    assert da._try_qt_modules() is None


def test_try_qt_modules_returns_none_when_matplotlib_qt_backend_missing(
        monkeypatch):
    """If the Qt binding is present but matplotlib lacks backend_qtagg,
    the probe must still return None instead of raising."""
    # Synthesize fake PyQt5 modules so the binding check succeeds.
    pkg = types.ModuleType("PyQt5")
    qt_widgets = types.ModuleType("PyQt5.QtWidgets")
    qt_core = types.ModuleType("PyQt5.QtCore")
    monkeypatch.setitem(sys.modules, "PyQt5", pkg)
    monkeypatch.setitem(sys.modules, "PyQt5.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "PyQt5.QtCore", qt_core)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "matplotlib.backends.backend_qtagg":
            raise ImportError("synthetic: no qtagg in this build")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert da._try_qt_modules() is None


def test_show_detections_empty_list_prints_and_returns(capsys):
    """No detections → friendly print, no exception, no GUI invoked."""
    da.show_detections([], cmds=["overview"], settings=None,
                       sample_rate=1.0, bit_depth=8)
    out = capsys.readouterr().out
    assert "No detections" in out


def test_show_detections_prefer_qt_false_uses_pyplot(monkeypatch):
    """prefer_qt=False must skip Qt probing entirely."""
    qt_calls = []
    pyplot_calls = []

    monkeypatch.setattr(da, "_try_qt_modules",
                        lambda: qt_calls.append("called") or None)
    monkeypatch.setattr(da, "_show_detections_pyplot",
                        lambda *a, **kw: pyplot_calls.append((a, kw)))

    da.show_detections([object()], cmds=["overview"], settings=None,
                       sample_rate=1.0, bit_depth=8, prefer_qt=False)

    assert qt_calls == [], "Qt probe must not run when prefer_qt=False"
    assert len(pyplot_calls) == 1


def test_show_detections_falls_back_when_no_qt(monkeypatch):
    """If _try_qt_modules returns None, pyplot path runs."""
    pyplot_calls = []

    monkeypatch.setattr(da, "_try_qt_modules", lambda: None)
    monkeypatch.setattr(da, "_show_detections_pyplot",
                        lambda *a, **kw: pyplot_calls.append((a, kw)))

    da.show_detections([object()], cmds=["overview"], settings=None,
                       sample_rate=1.0, bit_depth=8)

    assert len(pyplot_calls) == 1

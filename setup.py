#!/usr/bin/env python
"""Setuptools-based setup module (legacy — prefer pyproject.toml)."""

from pathlib import Path
from setuptools import setup, find_packages


def _read_version() -> str:
    """Read version from thriftyx/__init__.py (single source of truth)."""
    init_path = Path(__file__).parent / "thriftyx" / "__init__.py"
    for line in init_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", maxsplit=1)[1].strip().strip('"')
    raise RuntimeError("Unable to find __version__ in thriftyx/__init__.py")

setup(
    name='thriftyx',
    version=_read_version(),
    description='Airspy-based TDOA positioning for wildlife tracking',
    author='Schalk-Willem Krüger, Sungjoo Hwang',
    python_requires=">=3.10",
    install_requires=['numpy>=1.23', 'scipy>=1.9'],
    extras_require={
        'analysis': ['matplotlib>=3.6'],
        'fft': ['pyfftw>=0.13'],
        'dev': ['pytest>=7.0', 'pytest-cov', 'mypy', 'ruff'],
    },
    packages=find_packages(include=['thriftyx', 'thriftyx.*']),
    entry_points={
        'console_scripts': [
            'thriftyx = thriftyx.cli:_main',
            'thrifty = thriftyx.cli:_main',
        ]
    },
)

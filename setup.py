#!/usr/bin/env python
"""Setuptools-based setup module (legacy — prefer pyproject.toml)."""

from setuptools import setup, find_packages

setup(
    name='thriftyx',
    version='0.1.0',
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

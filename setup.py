"""Compatibility shim for tools that still expect a ``setup.py``.

All project metadata lives in ``pyproject.toml`` (PEP 621); setuptools reads it
from there. This file exists only so legacy invocations keep working:

    python setup.py --version
    python setup.py sdist

Modern installs need nothing from here:

    python -m pip install .

Prefer the ``pyproject.toml`` over adding arguments below - anything set in
both places can drift.
"""

from setuptools import setup

setup()

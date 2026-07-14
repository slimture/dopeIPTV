"""Guard against the recurring release bug: the app version in
dopeiptv/__init__.py and the packaging version in pyproject.toml MUST match,
or the built sdist/wheel gets named after the wrong version (e.g. a 0.7.1
release shipping a dopeiptv-0.7.0.tar.gz). This test fails the moment they
drift, so it's caught before tagging a release, not after.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import dopeiptv

_ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_version_matches_package() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as fh:
        pyproject_version = tomllib.load(fh)["project"]["version"]
    assert pyproject_version == dopeiptv.__version__, (
        f"Version drift: pyproject.toml says {pyproject_version!r} but "
        f"dopeiptv.__version__ is {dopeiptv.__version__!r}. Bump BOTH."
    )

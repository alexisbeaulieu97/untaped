"""``untaped.capability_api`` is the only SDK surface; the root re-exports nothing."""

import importlib
import subprocess
import sys

import pytest

import untaped


def test_package_root_has_no_star_export() -> None:
    """The root is not an SDK surface: no ``__all__`` for ``import *``."""
    assert "__all__" not in vars(untaped)


def test_root_no_longer_forwards_sdk_names() -> None:
    """``from untaped import X`` was removed in 8.0; import from ``capability_api``."""
    with pytest.raises(AttributeError):
        _ = untaped.ConfigError  # type: ignore[attr-defined]
    with pytest.raises(ImportError):
        from untaped import bounded_map  # type: ignore[attr-defined]  # noqa: F401


def test_api_shim_module_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("untaped.api")


def test_package_import_loads_no_sdk_modules() -> None:
    code = (
        "import sys, untaped\nprint(sorted(m for m in sys.modules if m.startswith('untaped.')))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "[]"

"""``untaped.capability_api`` is the SDK surface; the root only forwards (deprecated)."""

import subprocess
import sys

import pytest

import untaped
import untaped.api
import untaped.capability_api as capi


def test_package_root_has_no_star_export() -> None:
    """The root is not an SDK surface: no ``__all__`` for ``import *``."""
    assert "__all__" not in vars(untaped)


def test_deprecated_root_names_forward_lazily() -> None:
    """``from untaped import X`` keeps working (deprecated) for SDK names."""
    from untaped import ConfigError, bounded_map, get_settings

    assert ConfigError is capi.ConfigError
    assert bounded_map is capi.bounded_map
    assert get_settings is untaped.api.get_settings
    # ``app_context`` is also a submodule name; the submodule wins at the root.
    for name in {*capi.__all__, *untaped.api.__all__} - {"app_context"}:
        source = capi if name in capi.__all__ else untaped.api
        assert getattr(untaped, name) is getattr(source, name), name


def test_unknown_root_names_still_raise() -> None:
    with pytest.raises(AttributeError):
        _ = untaped.definitely_not_exported  # type: ignore[attr-defined]
    with pytest.raises(ImportError):
        from untaped import (
            definitely_not_exported,  # type: ignore[attr-defined]  # noqa: F401
        )


def test_package_import_loads_no_sdk_modules() -> None:
    code = (
        "import sys, untaped\nprint(sorted(m for m in sys.modules if m.startswith('untaped.')))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert proc.stdout.strip() == "[]"

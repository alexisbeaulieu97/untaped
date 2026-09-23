"""``untaped.capability_api`` is the SDK surface; the root only forwards (deprecated)."""

import subprocess
import sys

import pytest

import untaped
import untaped.api
import untaped.capability_api as capi
from untaped import cli, errors, prompts, ui


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


def test_render_rows_is_exported() -> None:
    assert capi.render_rows is cli.render_rows
    assert "render_rows" in capi.__all__


def test_ui_helpers_are_exported() -> None:
    assert capi.UiContext is ui.UiContext
    assert capi.ui_context is ui.ui_context


def test_prompt_choice_is_exported() -> None:
    assert capi.PromptChoice is prompts.PromptChoice
    assert "PromptChoice" in capi.__all__


def test_http_error_subclasses_are_exported() -> None:
    assert capi.HttpStatusError is errors.HttpStatusError
    assert capi.HttpTransportError is errors.HttpTransportError
    assert issubclass(capi.HttpStatusError, capi.HttpError)
    assert issubclass(capi.HttpTransportError, capi.HttpError)
    assert {"HttpStatusError", "HttpTransportError"} <= set(capi.__all__)


def test_shared_primitives_are_exported() -> None:
    for name in (
        "atomic_write",
        "bounded_map",
        "finish",
        "paginate_link",
        "parse_json_pairs",
        "read_structured_file",
        "resolve_text_input",
        "unified_diff_text",
        "StateCollection",
        "StateMap",
    ):
        assert hasattr(capi, name), name
        assert name in capi.__all__, name


def test_retired_names_are_not_exposed() -> None:
    """The retired plugin/profile-shim/logging names stay off the surface."""
    for name in (
        "ProfileOverrideOption",
        "profile_override",
        "DEFAULT_PROFILE",
        "ProfileSource",
        "resolve_profiles",
        "PluginManifest",
        "PluginRegistry",
        "SkillSpec",
        "PluginContext",
        "plugin_context",
        "get_logger",
        "configure_logging",
    ):
        assert not hasattr(capi, name), f"capability_api.{name} should be retired"
        assert name not in capi.__all__

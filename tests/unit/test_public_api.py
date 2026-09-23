"""The package root is not an SDK surface; ``untaped.capability_api`` is."""

import untaped
import untaped.capability_api as capi
from untaped import cli, errors, prompts, ui


def test_package_root_re_exports_nothing() -> None:
    """``from untaped import X`` is retired; the root only hosts submodules."""
    assert not hasattr(untaped, "__all__")
    for name in ("echo", "emit", "create_app", "ConfigError", "UntapedError", "bounded_map"):
        assert not hasattr(untaped, name), f"untaped.{name} must not be re-exported at the root"


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

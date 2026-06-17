import untaped
from untaped import cli, prompts, ui


def test_root_reexports_match_api_surface() -> None:
    """The package root re-exports exactly the ``untaped.api`` surface."""
    from untaped import api

    assert set(untaped.__all__) == set(api.__all__)


def test_render_rows_is_re_exported() -> None:
    assert untaped.render_rows is cli.render_rows
    assert "render_rows" in untaped.__all__


def test_ui_helpers_are_re_exported() -> None:
    assert untaped.UiContext is ui.UiContext
    assert untaped.ThemeSpec is ui.ThemeSpec
    assert untaped.ui_context is ui.ui_context


def test_prompt_choice_is_re_exported() -> None:
    assert untaped.PromptChoice is prompts.PromptChoice
    assert "PromptChoice" in untaped.__all__


def test_http_error_subclasses_are_re_exported() -> None:
    from untaped import errors

    assert untaped.HttpStatusError is errors.HttpStatusError
    assert untaped.HttpTransportError is errors.HttpTransportError
    assert issubclass(untaped.HttpStatusError, untaped.HttpError)
    assert issubclass(untaped.HttpTransportError, untaped.HttpError)
    assert {"HttpStatusError", "HttpTransportError"} <= set(untaped.__all__)


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
        assert not hasattr(untaped, name), f"untaped.{name} should be retired"
        assert name not in untaped.__all__

import untaped
from untaped import cli, prompts, ui


def test_profile_helpers_are_no_longer_exposed() -> None:
    """Profile support moved to the untaped-profile plugin (plugin API v4)."""
    for name in (
        "ProfileOverrideOption",
        "profile_override",
        "DEFAULT_PROFILE",
        "ProfileSource",
        "classify_active_profile",
        "effective_active_profile_name",
        "resolve_profiles",
        "profile_resolver",
    ):
        assert not hasattr(untaped, name), f"untaped.{name} should be gone"
        assert name not in untaped.__all__


def test_render_rows_is_re_exported() -> None:
    assert untaped.render_rows is cli.render_rows
    assert "render_rows" in untaped.__all__


def test_logging_helpers_are_no_longer_exposed() -> None:
    assert not hasattr(untaped, "get_logger")
    assert not hasattr(untaped, "configure_logging")
    assert "get_logger" not in untaped.__all__
    assert "configure_logging" not in untaped.__all__


def test_ui_helpers_are_re_exported() -> None:
    assert untaped.UiContext is ui.UiContext
    assert untaped.ThemeSpec is ui.ThemeSpec
    assert untaped.UiSettings is ui.UiSettings
    assert untaped.ui_context is ui.ui_context
    assert untaped.resolve_theme is ui.resolve_theme


def test_prompt_helpers_are_re_exported() -> None:
    assert untaped.PromptChoice is prompts.PromptChoice
    assert untaped.confirm is prompts.confirm
    assert untaped.text is prompts.text
    assert untaped.secret is prompts.secret
    assert untaped.select is prompts.select
    assert untaped.multiselect is prompts.multiselect

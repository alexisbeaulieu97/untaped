import untaped
from untaped import cli, profile_resolver, prompts, ui


def test_profile_resolver_helpers_are_re_exported() -> None:
    assert untaped.DEFAULT_PROFILE is profile_resolver.DEFAULT_PROFILE
    assert untaped.effective_active_profile_name is profile_resolver.effective_active_profile_name
    assert untaped.resolve_profiles is profile_resolver.resolve_profiles


def test_profile_override_helpers_are_re_exported() -> None:
    assert untaped.ProfileOverrideOption == cli.ProfileOverrideOption
    assert untaped.profile_override is cli.profile_override


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

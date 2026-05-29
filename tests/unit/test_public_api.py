import untaped
from untaped import cli, profile_resolver


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

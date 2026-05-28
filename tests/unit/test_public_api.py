import untaped
from untaped import profile_resolver


def test_profile_resolver_helpers_are_re_exported() -> None:
    assert untaped.DEFAULT_PROFILE is profile_resolver.DEFAULT_PROFILE
    assert untaped.effective_active_profile_name is profile_resolver.effective_active_profile_name
    assert untaped.resolve_profiles is profile_resolver.resolve_profiles
    assert untaped.splice_workspace_registry is profile_resolver.splice_workspace_registry


def test_logging_helpers_are_no_longer_exposed() -> None:
    assert not hasattr(untaped, "get_logger")
    assert not hasattr(untaped, "configure_logging")
    assert "get_logger" not in untaped.__all__
    assert "configure_logging" not in untaped.__all__

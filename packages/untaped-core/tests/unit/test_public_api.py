import untaped_core
from untaped_core import profile_resolver


def test_profile_resolver_helpers_are_re_exported() -> None:
    assert untaped_core.DEFAULT_PROFILE is profile_resolver.DEFAULT_PROFILE
    assert (
        untaped_core.effective_active_profile_name is profile_resolver.effective_active_profile_name
    )
    assert untaped_core.resolve_profiles is profile_resolver.resolve_profiles
    assert untaped_core.splice_workspace_registry is profile_resolver.splice_workspace_registry


def test_logging_helpers_are_no_longer_exposed() -> None:
    assert not hasattr(untaped_core, "get_logger")
    assert not hasattr(untaped_core, "configure_logging")
    assert "get_logger" not in untaped_core.__all__
    assert "configure_logging" not in untaped_core.__all__

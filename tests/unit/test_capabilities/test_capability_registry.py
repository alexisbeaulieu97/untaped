"""Tests for composition records and the happy-path pipeline (spec §§1-5)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from test_capabilities.capharness import (
    OtherProfile,
    Profile,
    State,
    exploding_check,
    function_provider,
    make_check,
    make_external,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.capabilities.registry import (
    CAPABILITY_API_VERSION,
    ApplicationSpec,
    CapabilityContext,
    CapabilitySpec,
    CompositionResult,
    DoctorCheck,
    DoctorResult,
    ExternalProvider,
    ProviderRef,
    QuarantineRecord,
    RegisteredCapability,
    SkillAsset,
    compose,
)
from untaped.errors import ConfigError


def test_api_version_is_one() -> None:
    assert CAPABILITY_API_VERSION == 1.0


def test_closed_shape_rejects_unknown_fields() -> None:
    with pytest.raises(TypeError):
        CapabilitySpec(  # type: ignore[call-arg]
            name="a",
            app_factory=None,  # type: ignore[arg-type]
            config_section="a",
            profile_model=Profile,
            bogus_field=1,
        )
    with pytest.raises(TypeError):
        ApplicationSpec(  # type: ignore[call-arg]
            name="s",
            app_factory=None,  # type: ignore[arg-type]
            config_section="s",
            profile_model=Profile,
            bogus_field=1,
        )
    with pytest.raises(TypeError):
        SkillAsset(name="s", source=Path("/tmp"), description="d", extra=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        DoctorCheck(id="a", title="t", run=lambda ctx: None, extra=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        DoctorResult(id="a", ok=True, detail="d", extra=1)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        CapabilityContext(  # type: ignore[call-arg]
            capability="a",
            config_section="a",
            profile_fields=frozenset(),
            state_fields=frozenset(),
            settings=None,
            extra=1,
        )
    with pytest.raises(TypeError):
        ProviderRef(  # type: ignore[call-arg]
            kind="built-in",
            distribution="u",
            entry_point="",
            api_requires=(1.0, 2.0),
            extra=1,
        )
    with pytest.raises(TypeError):
        QuarantineRecord(  # type: ignore[call-arg]
            distribution="d",
            entry_point="e",
            reason="api-range",
            detail="x",
            extra=1,
        )


def test_records_are_frozen() -> None:
    spec = make_spec()
    with pytest.raises(FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]
    asset = make_skill()
    with pytest.raises(FrozenInstanceError):
        asset.name = "other"  # type: ignore[misc]
    check = make_check()
    with pytest.raises(FrozenInstanceError):
        check.id = "other"  # type: ignore[misc]
    result = DoctorResult(id="a", ok=True, detail="d")
    with pytest.raises(FrozenInstanceError):
        result.ok = False  # type: ignore[misc]
    ctx = CapabilityContext(
        capability="a",
        config_section="a",
        profile_fields=frozenset({"x"}),
        state_fields=frozenset(),
        settings=None,
    )
    with pytest.raises(FrozenInstanceError):
        ctx.capability = "b"  # type: ignore[misc]
    ref = ProviderRef(
        kind="built-in", distribution="untaped", entry_point="", api_requires=(1.0, 2.0)
    )
    with pytest.raises(FrozenInstanceError):
        ref.kind = "external"  # type: ignore[misc]
    record = QuarantineRecord(distribution="d", entry_point="e", reason="api-range", detail="x")
    with pytest.raises(FrozenInstanceError):
        record.reason = "other"  # type: ignore[misc]


def test_skill_asset_construction_rules() -> None:
    with pytest.raises(ConfigError):
        SkillAsset(name="   ", source=Path("/tmp"), description="d")
    with pytest.raises(ConfigError):
        SkillAsset(name="s", source=Path("/tmp"), description="   ")


def test_spec_construction_rules() -> None:
    with pytest.raises(ConfigError):
        make_spec(name="   ")
    with pytest.raises(ConfigError):
        make_spec(section="  ")
    with pytest.raises(ConfigError):
        CapabilitySpec(
            name="a",
            app_factory=make_spec().app_factory,
            config_section="a",
            profile_model=dict,  # type: ignore[arg-type]
        )
    with pytest.raises(ConfigError):
        make_spec(state=dict)  # type: ignore[arg-type]


def test_spec_normalizes_sequences_to_tuples() -> None:
    spec = CapabilitySpec(
        name="a",
        app_factory=make_spec().app_factory,
        config_section="a",
        profile_model=Profile,
        skills=[make_skill("s1")],
        doctor_checks=[make_check("a.b")],
    )
    assert spec.skills == (make_skill("s1"),)
    assert isinstance(spec.skills, tuple)
    assert isinstance(spec.doctor_checks, tuple)


def test_provider_ref_rejects_unknown_kind() -> None:
    with pytest.raises(ConfigError):
        ProviderRef(kind="sidecar", distribution="d", entry_point="m:a", api_requires=(1.0, 2.0))


def test_quarantine_record_rejects_unknown_reason() -> None:
    with pytest.raises(ConfigError):
        QuarantineRecord(distribution="d", entry_point="e", reason="nope", detail="x")


def test_quarantine_record_rejects_empty_detail() -> None:
    with pytest.raises(ConfigError):
        QuarantineRecord(distribution="d", entry_point="e", reason="api-range", detail="  ")


def test_compose_happy_path() -> None:
    shell = make_shell()
    builtin = make_spec(name="github", profile=Profile, skills=(make_skill("gh-skill"),))
    ext_spec = make_spec(name="jira", profile=OtherProfile, checks=(make_check("jira.auth"),))
    result = compose(shell, [builtin], [make_external(ext_spec, "example-jira")])
    assert isinstance(result, CompositionResult)
    assert result.quarantine == ()
    assert [cap.spec.name for cap in result.capabilities] == ["github", "jira"]
    github, jira = result.capabilities
    assert isinstance(github, RegisteredCapability)
    assert github.spec is builtin
    assert github.skills == builtin.skills
    assert github.provider_ref.kind == "built-in"
    assert github.provider_ref.distribution == "untaped"
    assert github.provider_ref.entry_point == ""
    assert github.provider_ref.api_requires == (1.0, 2.0)
    assert jira.provider_ref.kind == "external"
    assert jira.provider_ref.distribution == "example-jira"
    assert jira.provider_ref.api_requires == (1.0, 2.0)


def test_compose_shell_only() -> None:
    result = compose(make_shell())
    assert result.capabilities == ()
    assert result.quarantine == ()


def test_compose_accepts_function_provider() -> None:
    spec = make_spec(name="func")
    candidate = ExternalProvider(
        distribution="fn-dist", name="func", target=function_provider(spec)
    )
    result = compose(make_shell(), [], [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["func"]
    assert result.quarantine == ()


def test_compose_never_runs_doctor_bodies() -> None:
    calls: list[str] = []
    spec = make_spec(
        name="watched",
        checks=(make_check("watched.ok", record_calls=calls), exploding_check()),
    )
    shell = make_shell(checks=(exploding_check("shell.boom"),))
    result = compose(shell, [spec], [make_external(make_spec(name="ext"))])
    assert calls == []
    assert [cap.spec.name for cap in result.capabilities] == ["watched", "ext"]


def test_compose_leaves_global_settings_registry_untouched() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    before_state = dict(_CONFIG_REGISTRY.state_sections)
    compose(
        make_shell(),
        [make_spec(name="built")],
        [make_external(make_spec(name="ext"))],
    )
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles
    assert dict(_CONFIG_REGISTRY.state_sections) == before_state


def test_failed_provider_registers_nothing() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    good = make_spec(
        name="good", skills=(make_skill("good-skill"),), checks=(make_check("good.ok"),)
    )
    bad = make_spec(name="bad", section="good")
    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    result = compose(
        make_shell(),
        [good],
        [
            make_external(bad, "bad-dist"),
            make_external(make_spec(name="later"), "later-dist"),
        ],
    )
    assert [cap.spec.name for cap in result.capabilities] == ["good", "later"]
    assert [q.reason for q in result.quarantine] == ["duplicate-section"]
    assert "good" in [cap.spec.name for cap in result.capabilities]
    registered_skills = [skill.name for cap in result.capabilities for skill in cap.skills]
    assert registered_skills == ["good-skill"]
    registered_checks = [
        check.id for cap in result.capabilities for check in cap.spec.doctor_checks
    ]
    assert registered_checks == ["good.ok"]
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles


def test_builtin_precedence_over_external_collision() -> None:
    builtin = make_spec(name="github")
    clash = make_spec(name="github", profile=OtherProfile)
    result = compose(make_shell(), [builtin], [make_external(clash, "ext-dist")])
    assert [cap.spec.name for cap in result.capabilities] == ["github"]
    assert result.capabilities[0].spec.profile_model is Profile
    assert len(result.quarantine) == 1
    assert result.quarantine[0].reason == "duplicate-name"
    assert "'github'" in result.quarantine[0].detail


def test_deterministic_external_ordering() -> None:
    specs = [make_spec(name=n) for n in ("zeta", "alpha", "mid")]
    externals = [
        make_external(specs[0], "b-dist"),
        make_external(specs[1], "a-dist"),
        make_external(specs[2], "a-dist"),
    ]
    result = compose(make_shell(), [], externals)
    ordered = [cap.spec.name for cap in result.capabilities]
    assert ordered == ["alpha", "mid", "zeta"]
    assert result.quarantine == ()


def test_builtins_keep_declaration_order_before_externals() -> None:
    first = make_spec(name="first")
    second = make_spec(name="second")
    ext = make_external(make_spec(name="aaa"), "z-dist")
    result = compose(make_shell(), [first, second], [ext])
    assert [cap.spec.name for cap in result.capabilities] == ["first", "second", "aaa"]


def test_quarantine_record_names_colliding_value() -> None:
    clash = make_spec(name="dup")
    result = compose(make_shell(), [make_spec(name="dup")], [make_external(clash, "ext-dist")])
    (record,) = result.quarantine
    assert record.distribution == "ext-dist"
    assert record.reason == "duplicate-name"
    assert "dup" in record.detail


def test_unresolvable_target_reports_empty_entry_point() -> None:
    candidate = ExternalProvider(
        distribution="ghost-dist",
        name="ghost",
        target="no_such_module_xyz:provider",
    )
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert record.entry_point == ""
    assert "no_such_module_xyz:provider" in record.detail


def test_provider_protocol_shape() -> None:
    from untaped.capabilities.registry import CapabilityProvider

    provider = function_provider(make_spec(name="proto"))
    assert callable(provider)
    assert provider.api_requires == (1.0, 2.0)
    assert isinstance(provider(), CapabilitySpec)
    assert isinstance(CapabilityProvider, type)


def test_spec_without_state_defaults() -> None:
    spec = make_spec()
    assert spec.state_model is None
    assert spec.skills == ()
    assert spec.doctor_checks == ()


def test_capability_context_snapshot_shape() -> None:
    settings = Profile()
    ctx = CapabilityContext(
        capability="alpha",
        config_section="alpha",
        profile_fields=frozenset(Profile.model_fields),
        state_fields=frozenset(State.model_fields),
        settings=settings,
    )
    assert ctx.profile_fields == frozenset({"token", "region"})
    assert ctx.state_fields == frozenset({"last_run"})
    assert ctx.settings is settings

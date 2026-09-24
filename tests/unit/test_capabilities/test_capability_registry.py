"""Tests for composition records and the happy-path pipeline (spec §§1-5)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from cyclopts import App

from test_capabilities.capharness import (
    OtherProfile,
    Profile,
    exploding_check,
    function_provider,
    make_app,
    make_check,
    make_external,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.capabilities.registry import (
    CapabilitySpec,
    CompositionResult,
    ExternalProvider,
    ProviderRef,
    QuarantineRecord,
    RegisteredCapability,
    SkillAsset,
    compose,
)
from untaped.errors import ConfigError


@pytest.mark.parametrize(
    "build",
    [
        lambda: SkillAsset(name="   ", source=Path("/tmp"), description="d"),
        lambda: SkillAsset(name="s", source=Path("/tmp"), description="   "),
        lambda: make_spec(name="   "),
        lambda: make_spec(section="  "),
        lambda: make_spec(profile=dict),
        lambda: make_spec(state=dict),
        lambda: ProviderRef(
            kind="sidecar", distribution="d", entry_point="m:a", api_requires=(1.0, 2.0)
        ),
        lambda: QuarantineRecord(distribution="d", entry_point="e", reason="nope", detail="x"),
        lambda: QuarantineRecord(distribution="d", entry_point="e", reason="api-range", detail=" "),
    ],
    ids=[
        "skill-blank-name",
        "skill-blank-description",
        "spec-blank-name",
        "spec-blank-section",
        "spec-profile-not-model",
        "spec-state-not-model",
        "ref-unknown-kind",
        "quarantine-unknown-reason",
        "quarantine-blank-detail",
    ],
)
def test_records_reject_invalid_construction(build: Callable[[], object]) -> None:
    with pytest.raises(ConfigError):
        build()


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


@pytest.mark.parametrize("bad_help", ["", "   ", "two\nlines", 42])
def test_capability_help_must_be_one_non_empty_line(bad_help: object) -> None:
    with pytest.raises(ConfigError, match="help must be a non-empty single line"):
        CapabilitySpec(
            name="alpha",
            app_factory=make_spec().app_factory,
            config_section="alpha",
            profile_model=Profile,
            help=bad_help,  # type: ignore[arg-type]
        )


def test_builtin_with_help_defers_its_factory() -> None:
    calls: list[str] = []

    def factory() -> App:
        calls.append("lazy")
        return make_app("lazy")

    lazy = CapabilitySpec(
        name="lazy",
        app_factory=factory,
        config_section="lazy",
        profile_model=Profile,
        help="Lazy capability.",
    )
    result = compose(make_shell(), [lazy, make_spec()])
    lazy_cap, eager_cap = result.capabilities
    assert calls == []
    assert lazy_cap.app is None
    assert isinstance(eager_cap.app, App)

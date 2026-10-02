"""Tests for composition records and the happy-path pipeline (spec §§1-5)."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

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
    ProviderCandidate,
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
        lambda: QuarantineRecord(
            name="n", distribution="d", entry_point="e", reason="nope", detail="x"
        ),
        lambda: QuarantineRecord(
            name="n", distribution="d", entry_point="e", reason="bad-metadata", detail=" "
        ),
    ],
    ids=[
        "skill-blank-name",
        "skill-blank-description",
        "spec-blank-name",
        "spec-blank-section",
        "spec-profile-not-model",
        "spec-state-not-model",
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


def test_compose_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    shell = make_shell()
    github_spec = make_spec(name="github", profile=Profile, skills=(make_skill("gh-skill"),))
    package = ModuleType("fake_github_package")
    package.provider = lambda: github_spec  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_github_package", package)
    jira_spec = make_spec(name="jira", profile=OtherProfile, checks=(make_check("jira.auth"),))
    result = compose(
        shell,
        [
            make_external(jira_spec, "example-jira"),
            ProviderCandidate(
                distribution="untaped", name="github", target="fake_github_package:provider"
            ),
        ],
    )
    assert isinstance(result, CompositionResult)
    assert result.quarantine == ()
    assert [cap.spec.name for cap in result.capabilities] == ["github", "jira"]
    github, jira = result.capabilities
    assert isinstance(github, RegisteredCapability)
    assert github.spec is github_spec
    assert github.skills == github_spec.skills
    assert github.provider_ref == ProviderRef(
        distribution="untaped", entry_point="fake_github_package:provider"
    )
    assert jira.provider_ref == ProviderRef(distribution="example-jira", entry_point="jira")


def test_compose_shell_only() -> None:
    result = compose(make_shell())
    assert result.capabilities == ()
    assert result.quarantine == ()


def test_compose_accepts_function_provider() -> None:
    spec = make_spec(name="func")
    candidate = ProviderCandidate(
        distribution="fn-dist", name="func", target=function_provider(spec)
    )
    result = compose(make_shell(), [candidate])
    assert [cap.spec.name for cap in result.capabilities] == ["func"]
    assert result.quarantine == ()


def test_compose_never_runs_doctor_bodies() -> None:
    calls: list[str] = []
    spec = make_spec(
        name="watched",
        checks=(make_check("watched.ok", record_calls=calls), exploding_check()),
    )
    shell = make_shell(checks=(exploding_check("shell.boom"),))
    result = compose(shell, [make_external(spec), make_external(make_spec(name="ext"))])
    assert calls == []
    assert [cap.spec.name for cap in result.capabilities] == ["ext", "watched"]


def test_compose_leaves_global_settings_registry_untouched() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    before_state = dict(_CONFIG_REGISTRY.state_sections)
    compose(
        make_shell(),
        [make_external(make_spec(name="built")), make_external(make_spec(name="ext"))],
    )
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles
    assert dict(_CONFIG_REGISTRY.state_sections) == before_state


def test_failed_provider_registers_nothing() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    good = make_spec(
        name="good", skills=(make_skill("good-skill"),), checks=(make_check("good.ok"),)
    )
    bad = make_spec(name="worse", section="good")
    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    result = compose(
        make_shell(),
        [
            make_external(bad, "bad-dist"),
            make_external(good, "good-dist"),
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


def test_capabilities_compose_in_name_order_whatever_the_distribution() -> None:
    zeta = make_external(make_spec(name="zeta", section="zeta"), "aaa-dist")
    alpha = make_external(make_spec(name="alpha", section="alpha"), "zzz-dist")
    result = compose(make_shell(), [zeta, alpha])
    assert [c.spec.name for c in result.capabilities] == ["alpha", "zeta"]
    assert result.quarantine == ()


def test_a_duplicate_name_quarantines_the_later_distribution() -> None:
    first = make_external(make_spec(name="github", section="github"), "acme-dist")
    second = make_external(make_spec(name="github", section="github2"), "untaped")
    result = compose(make_shell(), [second, first])
    assert [c.provider_ref.distribution for c in result.capabilities] == ["acme-dist"]
    [record] = result.quarantine
    assert (record.distribution, record.reason) == ("untaped", "duplicate-name")
    assert record.detail == "duplicate capability name: 'github' (already provided by 'acme-dist')"


def test_a_duplicate_section_names_the_distribution_that_owns_it() -> None:
    owner = make_external(make_spec(name="alpha", section="shared"), "owner-dist")
    late = make_external(make_spec(name="beta", section="shared"), "late-dist")
    result = compose(make_shell(), [late, owner])
    [record] = result.quarantine
    assert (record.distribution, record.reason) == ("late-dist", "duplicate-section")
    assert record.detail == "duplicate config section: 'shared' (already provided by 'owner-dist')"


def test_a_collision_with_the_shell_names_the_shell() -> None:
    result = compose(
        make_shell(),
        [
            make_external(make_spec(name="untaped", section="mine"), "a-dist"),
            make_external(make_spec(name="intruder", section="shell"), "b-dist"),
        ],
    )
    assert result.capabilities == ()
    assert [record.detail for record in result.quarantine] == [
        "duplicate config section: 'shell' (already provided by the shell)",
        "duplicate capability name: 'untaped' (already provided by the shell)",
    ]


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


def test_a_provider_with_help_is_deferred_and_one_without_is_staged() -> None:
    calls: list[str] = []

    def factory() -> App:
        calls.append("lazy")
        return make_app("lazy")

    lazy = make_external(
        replace(make_spec(name="lazy", section="lazy", factory=factory), help="Lazy.")
    )
    eager = make_external(make_spec(name="eager", section="eager"))
    result = compose(make_shell(), [lazy, eager])
    staged = {c.spec.name: c.app for c in result.capabilities}
    assert staged["lazy"] is None
    assert calls == []
    assert isinstance(staged["eager"], App)

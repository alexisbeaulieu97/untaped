"""Tests for composition records and the happy-path pipeline."""

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
    make_candidate,
    make_check,
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
            make_candidate(jira_spec, "example-jira"),
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
    result = compose(shell, [make_candidate(spec), make_candidate(make_spec(name="ext"))])
    assert calls == []
    assert [cap.spec.name for cap in result.capabilities] == ["ext", "watched"]


def test_compose_leaves_global_settings_registry_untouched() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    before_state = dict(_CONFIG_REGISTRY.state_sections)
    compose(
        make_shell(),
        [make_candidate(make_spec(name="alpha")), make_candidate(make_spec(name="beta"))],
    )
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles
    assert dict(_CONFIG_REGISTRY.state_sections) == before_state


def test_failed_provider_registers_nothing() -> None:
    from untaped.settings import _CONFIG_REGISTRY

    good = make_spec(
        name="good", skills=(make_skill("good-skill"),), checks=(make_check("good.ok"),)
    )
    bad = make_spec(name="worse", section="shell")
    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    result = compose(
        make_shell(),
        [
            make_candidate(bad, "bad-dist"),
            make_candidate(good, "good-dist"),
            make_candidate(make_spec(name="later"), "later-dist"),
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
    zeta = make_candidate(make_spec(name="zeta", section="zeta"), "aaa-dist")
    alpha = make_candidate(make_spec(name="alpha", section="alpha"), "zzz-dist")
    result = compose(make_shell(), [zeta, alpha])
    assert [c.spec.name for c in result.capabilities] == ["alpha", "zeta"]
    assert result.quarantine == ()


def _github_rivals() -> list[ProviderCandidate]:
    return [
        make_candidate(make_spec(name="github", section="github"), "acme-dist"),
        make_candidate(make_spec(name="github", section="github2"), "untaped"),
        make_candidate(make_spec(name="jira", section="jira"), "jira-dist"),
    ]


def test_a_contested_name_quarantines_every_claimant() -> None:
    result = compose(make_shell(), _github_rivals())
    assert [c.spec.name for c in result.capabilities] == ["jira"]
    detail = "duplicate capability name: 'github' (claimed by 'acme-dist', 'untaped')"
    assert [(q.distribution, q.reason, q.detail) for q in result.quarantine] == [
        ("acme-dist", "duplicate-name", detail),
        ("untaped", "duplicate-name", detail),
    ]


def test_a_contested_name_does_not_depend_on_candidate_order() -> None:
    forward = compose(make_shell(), _github_rivals())
    backward = compose(make_shell(), list(reversed(_github_rivals())))
    assert [c.spec.name for c in backward.capabilities] == ["jira"]
    assert backward.quarantine == forward.quarantine


def test_a_contested_section_quarantines_every_claimant() -> None:
    owner = make_candidate(make_spec(name="alpha", section="shared"), "owner-dist")
    late = make_candidate(make_spec(name="beta", section="shared"), "late-dist")
    result = compose(make_shell(), [late, owner])
    assert result.capabilities == ()
    detail = "duplicate config section: 'shared' (claimed by 'late-dist', 'owner-dist')"
    assert [(q.name, q.distribution, q.reason, q.detail) for q in result.quarantine] == [
        ("alpha", "owner-dist", "duplicate-section", detail),
        ("beta", "late-dist", "duplicate-section", detail),
    ]


def test_three_claimants_are_all_quarantined() -> None:
    claimants = [
        make_candidate(make_spec(name="github", section=f"gh{index}"), dist)
        for index, dist in enumerate(("c-dist", "a-dist", "b-dist"))
    ]
    result = compose(make_shell(), claimants)
    assert result.capabilities == ()
    assert [(q.distribution, q.reason) for q in result.quarantine] == [
        ("a-dist", "duplicate-name"),
        ("b-dist", "duplicate-name"),
        ("c-dist", "duplicate-name"),
    ]
    assert {q.detail for q in result.quarantine} == {
        "duplicate capability name: 'github' (claimed by 'a-dist', 'b-dist', 'c-dist')"
    }


def test_a_claimant_of_a_contested_name_and_section_gets_one_name_record() -> None:
    first = make_candidate(make_spec(name="github", section="shared"), "a-dist")
    second = make_candidate(make_spec(name="github", section="github"), "b-dist")
    third = make_candidate(make_spec(name="other", section="shared"), "c-dist")
    result = compose(make_shell(), [third, second, first])
    assert result.capabilities == ()
    assert [(q.name, q.distribution, q.reason) for q in result.quarantine] == [
        ("github", "a-dist", "duplicate-name"),
        ("github", "b-dist", "duplicate-name"),
        ("other", "c-dist", "duplicate-section"),
    ]
    assert result.quarantine[2].detail == (
        "duplicate config section: 'shared' (claimed by 'a-dist', 'c-dist')"
    )


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        (
            make_candidate(make_spec(name="github"), "a-dist", error=ImportError("no")),
            "malformed-entry-point",
        ),
        (make_candidate(make_spec(name="github", section="shell"), "a-dist"), "duplicate-section"),
    ],
    ids=["provider-raises", "declaration-fails"],
)
def test_a_candidate_failing_its_own_checks_is_not_a_claimant(
    broken: ProviderCandidate, reason: str
) -> None:
    sound = make_candidate(make_spec(name="github"), "b-dist")
    result = compose(make_shell(), [broken, sound])
    assert [c.provider_ref.distribution for c in result.capabilities] == ["b-dist"]
    assert [(q.distribution, q.reason) for q in result.quarantine] == [("a-dist", reason)]


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

    lazy = make_candidate(
        replace(make_spec(name="lazy", section="lazy", factory=factory), help="Lazy.")
    )
    eager = make_candidate(make_spec(name="eager", section="eager"))
    result = compose(make_shell(), [lazy, eager])
    staged = {c.spec.name: c.app for c in result.capabilities}
    assert staged["lazy"] is None
    assert calls == []
    assert isinstance(staged["eager"], App)

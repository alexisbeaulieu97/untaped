"""Tests for composition records and the happy-path pipeline."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest
from cyclopts import App

from test_plugins.plugin_harness import (
    OtherProfile,
    Profile,
    exploding_check,
    make_app,
    make_candidate,
    make_check,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.errors import ConfigError
from untaped.plugins.registry import (
    CompositionResult,
    PluginCandidate,
    PluginRef,
    PluginSpec,
    QuarantineRecord,
    RegisteredPlugin,
    SkillAsset,
    compose,
    plugin_dir,
    run_deferred_factory,
    settings_model,
)
from untaped.records import DuplicateKindError, Record


@pytest.mark.parametrize(
    "build",
    [
        lambda: SkillAsset(name="   ", source=Path("/tmp"), description="d"),
        lambda: SkillAsset(name="s", source=Path("/tmp"), description="   "),
        lambda: make_spec(name="   "),
        lambda: make_spec(name="Acme"),
        lambda: make_spec(name="acme_tools"),
        lambda: make_spec(name="acme--tools"),
        lambda: make_spec(name="github@ghes"),
        lambda: make_spec(name=""),
        lambda: make_spec(name="9tools"),
        lambda: make_spec(name="-acme"),
        lambda: make_spec(name="acme-"),
        lambda: PluginSpec(name="quiet", help="Has no commands."),
        lambda: make_spec(settings=dict),
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
        "spec-name-uppercase",
        "spec-name-underscore",
        "spec-name-double-hyphen",
        "spec-name-at",
        "spec-name-empty",
        "spec-name-leading-digit",
        "spec-name-leading-hyphen",
        "spec-name-trailing-hyphen",
        "spec-help-without-commands",
        "spec-settings-not-model",
        "spec-state-not-model",
        "quarantine-unknown-reason",
        "quarantine-blank-detail",
    ],
)
def test_records_reject_invalid_construction(build: Callable[[], object]) -> None:
    with pytest.raises(ConfigError):
        build()


def test_spec_normalizes_sequences_to_tuples() -> None:
    spec = PluginSpec(
        name="a",
        app_factory=make_spec().app_factory,
        settings=Profile,
        skills=[make_skill("s1")],
        doctor_checks=[make_check("a.b")],
    )
    assert spec.skills == (make_skill("s1"),)
    assert isinstance(spec.skills, tuple)
    assert isinstance(spec.doctor_checks, tuple)


def test_compose_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    shell = make_shell()
    github_spec = make_spec(name="github", settings=Profile, skills=(make_skill("gh-skill"),))
    package = ModuleType("fake_github_package")
    package.SPEC = github_spec  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_github_package", package)
    jira_spec = make_spec(name="jira", settings=OtherProfile, checks=(make_check("jira.auth"),))
    result = compose(
        shell,
        [
            make_candidate(jira_spec, "example-jira"),
            PluginCandidate(
                distribution="untaped", name="github", target="fake_github_package:SPEC"
            ),
        ],
    )
    assert isinstance(result, CompositionResult)
    assert result.quarantine == ()
    assert [cap.spec.name for cap in result.plugins] == ["github", "jira"]
    github, jira = result.plugins
    assert isinstance(github, RegisteredPlugin)
    assert github.spec is github_spec
    assert github.skills == github_spec.skills
    assert github.plugin_ref == PluginRef(
        distribution="untaped", entry_point="fake_github_package:SPEC"
    )
    assert jira.plugin_ref == PluginRef(distribution="example-jira", entry_point="jira")


def test_compose_shell_only() -> None:
    result = compose(make_shell())
    assert result.plugins == ()
    assert result.quarantine == ()


def test_compose_refuses_a_callable_entry_point_without_calling_it() -> None:
    calls: list[str] = []

    def provider() -> PluginSpec:
        calls.append("called")
        return make_spec(name="func")

    candidate = PluginCandidate(distribution="fn-dist", name="func", target=provider)
    result = compose(make_shell(), [candidate])
    assert result.plugins == ()
    (record,) = result.quarantine
    assert record.reason == "not-a-spec"
    assert "names the callable" in record.detail
    assert "'untaped_func:SPEC'" in record.detail
    assert calls == []


def test_compose_never_runs_doctor_bodies() -> None:
    calls: list[str] = []
    spec = make_spec(
        name="watched",
        checks=(make_check("watched.ok", record_calls=calls), exploding_check()),
    )
    shell = make_shell(checks=(exploding_check("shell.boom"),))
    result = compose(shell, [make_candidate(spec), make_candidate(make_spec(name="ext"))])
    assert calls == []
    assert [cap.spec.name for cap in result.plugins] == ["ext", "watched"]


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
    bad = make_spec(name="shell")
    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    result = compose(
        make_shell(),
        [
            make_candidate(bad, "bad-dist"),
            make_candidate(good, "good-dist"),
            make_candidate(make_spec(name="later"), "later-dist"),
        ],
    )
    assert [cap.spec.name for cap in result.plugins] == ["good", "later"]
    assert [q.reason for q in result.quarantine] == ["reserved-name"]
    assert "good" in [cap.spec.name for cap in result.plugins]
    registered_skills = [skill.name for cap in result.plugins for skill in cap.skills]
    assert registered_skills == ["good-skill"]
    registered_checks = [check.id for cap in result.plugins for check in cap.spec.doctor_checks]
    assert registered_checks == ["good.ok"]
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles


def test_plugins_compose_in_name_order_whatever_the_distribution() -> None:
    zeta = make_candidate(make_spec(name="zeta"), "aaa-dist")
    alpha = make_candidate(make_spec(name="alpha"), "zzz-dist")
    result = compose(make_shell(), [zeta, alpha])
    assert [c.spec.name for c in result.plugins] == ["alpha", "zeta"]
    assert result.quarantine == ()


def _github_rivals() -> list[PluginCandidate]:
    return [
        make_candidate(make_spec(name="github"), "acme-dist"),
        make_candidate(make_spec(name="github"), "untaped"),
        make_candidate(make_spec(name="jira"), "jira-dist"),
    ]


def test_a_contested_name_quarantines_every_claimant() -> None:
    result = compose(make_shell(), _github_rivals())
    assert [c.spec.name for c in result.plugins] == ["jira"]
    detail = "duplicate plugin name: 'github' (claimed by 'acme-dist', 'untaped')"
    assert [(q.distribution, q.reason, q.detail) for q in result.quarantine] == [
        ("acme-dist", "duplicate-name", detail),
        ("untaped", "duplicate-name", detail),
    ]


def test_a_contested_name_does_not_depend_on_candidate_order() -> None:
    forward = compose(make_shell(), _github_rivals())
    backward = compose(make_shell(), list(reversed(_github_rivals())))
    assert [c.spec.name for c in backward.plugins] == ["jira"]
    assert backward.quarantine == forward.quarantine


def test_three_claimants_are_all_quarantined() -> None:
    claimants = [
        make_candidate(make_spec(name="github"), dist)
        for index, dist in enumerate(("c-dist", "a-dist", "b-dist"))
    ]
    result = compose(make_shell(), claimants)
    assert result.plugins == ()
    assert [(q.distribution, q.reason) for q in result.quarantine] == [
        ("a-dist", "duplicate-name"),
        ("b-dist", "duplicate-name"),
        ("c-dist", "duplicate-name"),
    ]
    assert {q.detail for q in result.quarantine} == {
        "duplicate plugin name: 'github' (claimed by 'a-dist', 'b-dist', 'c-dist')"
    }


@pytest.mark.parametrize(
    ("broken", "reason"),
    [
        (
            make_candidate(make_spec(name="github"), "a-dist", error=ImportError("no")),
            "malformed-entry-point",
        ),
        (
            make_candidate(
                make_spec(name="github"),
                "a-dist",
                error=DuplicateKindError("duplicate-kind: 'github.repo' is declared twice"),
            ),
            "duplicate-kind",
        ),
        (
            make_candidate(make_spec(name="github", skills=("not-a-skill",)), "a-dist"),  # type: ignore[arg-type]
            "bad-skill-asset",
        ),
    ],
    ids=["provider-raises", "kind-declared-twice", "declaration-fails"],
)
def test_a_candidate_failing_its_own_checks_is_not_a_claimant(
    broken: PluginCandidate, reason: str
) -> None:
    sound = make_candidate(make_spec(name="github"), "b-dist")
    result = compose(make_shell(), [broken, sound])
    assert [c.plugin_ref.distribution for c in result.plugins] == ["b-dist"]
    assert [(q.distribution, q.reason) for q in result.quarantine] == [("a-dist", reason)]


@pytest.mark.parametrize("bad_help", ["", "   ", "two\nlines", 42])
def test_plugin_help_must_be_one_non_empty_line(bad_help: object) -> None:
    with pytest.raises(ConfigError, match="help must be a non-empty single line"):
        PluginSpec(
            name="alpha",
            app_factory=make_spec().app_factory,
            settings=Profile,
            help=bad_help,  # type: ignore[arg-type]
        )


def test_a_provider_with_help_is_deferred_and_one_without_is_staged() -> None:
    calls: list[str] = []

    def factory() -> App:
        calls.append("lazy")
        return make_app("lazy")

    lazy = make_candidate(replace(make_spec(name="lazy", factory=factory), help="Lazy."))
    eager = make_candidate(make_spec(name="eager"))
    result = compose(make_shell(), [lazy, eager])
    staged = {c.spec.name: c.app for c in result.plugins}
    assert staged["lazy"] is None
    assert calls == []
    assert isinstance(staged["eager"], App)


def test_a_name_with_an_at_says_it_is_reserved() -> None:
    with pytest.raises(ConfigError, match=r"must not contain '@' \(reserved\)"):
        PluginSpec(name="github@ghes")


def test_every_part_of_a_spec_but_its_name_is_optional() -> None:
    result = compose(make_shell(), [make_candidate(PluginSpec(name="bare"))])

    [bare] = result.plugins
    assert (bare.spec.name, bare.app, result.quarantine) == ("bare", None, ())
    assert settings_model(bare.spec).model_fields == {}


def test_an_app_factory_must_be_callable() -> None:
    with pytest.raises(ConfigError, match="app_factory must be callable or None"):
        PluginSpec(name="bare", app_factory="not callable")  # type: ignore[arg-type]


def test_asking_a_plugin_without_commands_for_its_app_is_a_bug() -> None:
    [bare] = compose(make_shell(), [make_candidate(PluginSpec(name="bare"))]).plugins

    with pytest.raises(ConfigError, match="plugin 'bare' has no commands to build"):
        run_deferred_factory(bare)


def test_a_plugin_keeps_its_data_under_its_own_name() -> None:
    expected = Path.home() / ".untaped" / "plugins" / "acme-tools"
    assert plugin_dir(PluginSpec(name="acme-tools")) == expected


def test_a_plugin_whose_import_redeclares_a_kind_is_quarantined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "untaped_kind_thief.py").write_text(
        "from untaped.sdk import Record\n\n\n"
        'class Stolen(Record, kind="untaped.setting"):\n'
        "    key: str\n\n\n"
        "def provide(): ...\n"
    )
    monkeypatch.syspath_prepend(tmp_path)
    candidate = PluginCandidate(
        distribution="thief-dist", name="thief", target="untaped_kind_thief:provide"
    )

    result = compose(make_shell(), [candidate])

    assert [(q.name, q.reason) for q in result.quarantine] == [("thief", "duplicate-kind")]
    assert "untaped.config.models.SettingRow" in result.quarantine[0].detail


def test_a_plugin_whose_app_factory_redeclares_a_kind_is_quarantined() -> None:
    def factory() -> App:
        class Stolen(Record, kind="untaped.setting"):  # a lazily imported record module
            key: str

        return App()

    result = compose(make_shell(), [make_candidate(make_spec(name="thief", factory=factory))])

    assert [(q.name, q.reason) for q in result.quarantine] == [("thief", "duplicate-kind")]

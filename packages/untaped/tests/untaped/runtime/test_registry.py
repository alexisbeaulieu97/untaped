"""Tests for composition records and the happy-path pipeline."""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from core_test_support.commands.support import make_plugin
from core_test_support.runtime.plugin_harness import (
    OtherProfile,
    Profile,
    make_candidate,
)
from untaped.runtime.composed import ComposedPlugin
from untaped.runtime.registry import (
    CompositionResult,
    PluginCandidate,
    PluginRef,
    QuarantineRecord,
    _core_alone,
    compose,
    kept_declaration,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.health.doctor import DoctorCheck, DoctorResult


@pytest.mark.parametrize(
    "build",
    [
        lambda: QuarantineRecord(
            name="n", distribution="d", entry_point="e", reason="nope", detail="x"
        ),
        lambda: QuarantineRecord(
            name="n", distribution="d", entry_point="e", reason="bad-metadata", detail=" "
        ),
    ],
    ids=[
        "quarantine-unknown-reason",
        "quarantine-blank-detail",
    ],
)
def test_records_reject_invalid_construction(build: Callable[[], object]) -> None:
    with pytest.raises(ConfigError):
        build()


def test_compose_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # resolved by its entry point, as an installed plugin is
    make_plugin("github", settings=Profile, skill="Teach agents to use github.")
    jira = make_plugin("jira", settings=OtherProfile, auth=_runs())
    result = compose(
        [
            make_candidate(jira, "example-jira"),
            PluginCandidate(
                distribution="untaped", name="github", target="untaped_double_github.plugin:Github"
            ),
        ],
    )
    assert isinstance(result, CompositionResult)
    assert result.quarantine == ()
    assert [cap.name for cap in result.plugins] == ["untaped", "github", "jira"]
    _, composed_github, composed_jira = result.plugins
    assert isinstance(composed_github, ComposedPlugin)
    assert composed_github.skill is not None
    assert composed_github.skill.name == "untaped-github"
    assert composed_github.plugin_ref == PluginRef(
        distribution="untaped", entry_point="untaped_double_github.plugin:Github"
    )
    assert composed_jira.plugin_ref == PluginRef(distribution="example-jira", entry_point="jira")


def test_compose_core_only() -> None:
    result = compose()
    assert [plugin.name for plugin in result.plugins] == ["untaped"]
    assert result.quarantine == ()


def test_core_is_the_first_plugin_and_the_one_marked_root() -> None:
    """Core is one of the plugins, first, the root its declaration marks, with no entry point."""
    from untaped.core.plugin import Core
    from untaped.runtime.declaration import target_of

    result = compose([make_candidate(make_plugin("later"))])
    assert [(each.name, each.root) for each in result.plugins] == [
        ("untaped", True),
        ("later", False),
    ]
    core = result.plugins[0]
    assert core.plugin_ref == PluginRef(distribution="untaped", entry_point=target_of(Core))
    # its commands mount at the root from the declaration: no app is built under its name
    assert not core.has_commands


def test_core_is_read_as_a_composed_plugin() -> None:
    """Core is read from its declaration as a plugin is."""
    from untaped.core.settings import NAME, CoreSettings

    core = compose().plugins[0]
    assert (core.name, core.settings) == (NAME, CoreSettings)
    assert f"{NAME}.token-store" in [check.id for check in core.doctor_checks]
    # core's stub skill is named after it, not ``untaped-untaped``
    assert core.skill is not None
    assert core.skill.name == NAME


def _read(core: ComposedPlugin) -> tuple[object, ...]:
    return (
        core.name,
        core.settings,
        tuple(check.id for check in core.doctor_checks),
        core.skill,
        tuple(cache.qualified for cache in core.caches),
    )


def test_core_reads_the_same_from_a_kept_declaration() -> None:
    """A warm composition (the kept declaration, no class composed) reads core the same."""
    cold = compose()
    assert kept_declaration(()) is not None
    warm = compose()
    assert warm.mount.declaration == cold.mount.declaration
    assert _read(warm.plugins[0]) == _read(cold.plugins[0])
    assert warm.plugins[0].plugin_ref == cold.plugins[0].plugin_ref


def test_broken_key_declarations_in_core_s_settings_fail_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Core's settings get the key checks a plugin's get; a broken one is a core bug, raised."""
    from untaped.core.settings import CoreSettings

    monkeypatch.setattr(
        CoreSettings, "renamed_keys", {"http.timeout_seconds": "nope.missing"}, raising=False
    )
    _core_alone.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="core's own class does not compose"):
            compose()
    finally:
        monkeypatch.undo()
        _core_alone.cache_clear()


def test_compose_refuses_a_callable_entry_point_without_calling_it() -> None:
    calls: list[str] = []

    def provider() -> object:
        calls.append("called")
        return make_plugin("func")

    candidate = PluginCandidate(distribution="fn-dist", name="func", target=provider)
    result = compose([candidate])
    assert [plugin.name for plugin in result.plugins] == ["untaped"]
    (record,) = result.quarantine
    assert record.reason == "not-a-plugin-class"
    assert "names the callable" in record.detail
    assert "'untaped_func.plugin:Func'" in record.detail
    assert calls == []


def test_compose_hints_the_class_an_11x_entry_point_should_name() -> None:
    cls = make_plugin("old-tool")
    candidate = make_candidate(cls, "old-dist", result=SimpleNamespace(name="old-tool"))
    package = candidate.target.partition(".")[0]  # type: ignore[union-attr]
    (record,) = compose([candidate]).quarantine
    assert record.reason == "not-a-plugin-class"
    assert "names a SimpleNamespace" in record.detail
    assert f"'{package}.plugin:OldTool'" in record.detail


@pytest.mark.parametrize(
    "name", ["Acme", "acme_tools", "acme--tools", "github@ghes", "9tools", "-acme", "acme-"]
)
def test_a_malformed_entry_point_name_is_quarantined_before_anything_is_imported(
    name: str,
) -> None:
    calls: list[str] = []
    candidate = make_candidate(make_plugin("fine"), "odd-dist", name, calls=calls)
    result = compose([candidate])
    assert [plugin.name for plugin in result.plugins] == ["untaped"]
    (record,) = result.quarantine
    assert (record.reason, record.name) == ("bad-metadata", name)
    assert f"plugin name {name!r}" in record.detail
    assert calls == []


def _runs(calls: list[str] | None = None) -> DoctorCheck:
    """A plugin class's check that records each run in ``calls``, or must never run."""

    def run(_ctx: object) -> DoctorResult:
        if calls is None:
            raise AssertionError("doctor body must never run at compose time")
        calls.append("ran")
        return DoctorResult(id=None, ok=True, detail="fine")

    return DoctorCheck(title="Runs", run=run)


def test_compose_never_runs_doctor_bodies() -> None:
    calls: list[str] = []
    watched = make_plugin("watched", ok=_runs(calls), boom=_runs())
    result = compose([make_candidate(watched), make_candidate(make_plugin("ext"))])
    assert calls == []
    assert [cap.name for cap in result.plugins] == ["untaped", "ext", "watched"]


def test_compose_leaves_global_settings_registry_untouched() -> None:
    from untaped.runtime.settings import _CONFIG_REGISTRY

    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    compose(
        [make_candidate(make_plugin("alpha")), make_candidate(make_plugin("beta"))],
    )
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles


def test_failed_provider_registers_nothing() -> None:
    from untaped.runtime.settings import _CONFIG_REGISTRY

    good = make_plugin("good", skill="Teach agents to use good.", ok=_runs([]))
    bad = make_plugin("untaped")
    before_profiles = dict(_CONFIG_REGISTRY.profile_sections)
    result = compose(
        [
            make_candidate(bad, "bad-dist"),
            make_candidate(good, "good-dist"),
            make_candidate(make_plugin("later"), "later-dist"),
        ],
    )
    assert [cap.name for cap in result.plugins] == ["untaped", "good", "later"]
    assert [q.reason for q in result.quarantine] == ["duplicate-name"]
    added = [cap for cap in result.plugins if not cap.root]
    registered_skills = [cap.skill.name for cap in added if cap.skill is not None]
    assert registered_skills == ["untaped-good"]
    registered_checks = [check.id for cap in added for check in cap.doctor_checks]
    assert registered_checks == ["good.ok"]
    assert dict(_CONFIG_REGISTRY.profile_sections) == before_profiles


def test_plugins_compose_in_name_order_whatever_the_distribution() -> None:
    zeta = make_candidate(make_plugin("zeta"), "aaa-dist")
    alpha = make_candidate(make_plugin("alpha"), "zzz-dist")
    result = compose([zeta, alpha])
    assert [c.name for c in result.plugins] == ["untaped", "alpha", "zeta"]
    assert result.quarantine == ()


def _github_rivals() -> list[PluginCandidate]:
    return [
        make_candidate(make_plugin("github"), "acme-dist"),
        make_candidate(make_plugin("github"), "untaped"),
        make_candidate(make_plugin("jira"), "jira-dist"),
    ]


def test_a_contested_name_quarantines_every_claimant() -> None:
    result = compose(_github_rivals())
    assert [c.name for c in result.plugins] == ["untaped", "jira"]
    detail = "duplicate plugin name: 'github' (claimed by 'acme-dist', 'untaped')"
    assert [(q.distribution, q.reason, q.detail) for q in result.quarantine] == [
        ("acme-dist", "duplicate-name", detail),
        ("untaped", "duplicate-name", detail),
    ]


def test_a_contested_name_does_not_depend_on_candidate_order() -> None:
    forward = compose(_github_rivals())
    backward = compose(list(reversed(_github_rivals())))
    assert [c.name for c in backward.plugins] == ["untaped", "jira"]
    assert backward.quarantine == forward.quarantine


def test_three_claimants_are_all_quarantined() -> None:
    claimants = [
        make_candidate(make_plugin("github"), dist) for dist in ("c-dist", "a-dist", "b-dist")
    ]
    result = compose(claimants)
    assert [plugin.name for plugin in result.plugins] == ["untaped"]
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
            make_candidate(make_plugin("github"), "a-dist", error=ImportError("no")),
            "malformed-entry-point",
        ),
    ],
    ids=["provider-raises"],
)
def test_a_candidate_failing_its_own_checks_is_not_a_claimant(
    broken: PluginCandidate, reason: str
) -> None:
    sound = make_candidate(make_plugin("github"), "b-dist")
    result = compose([broken, sound])
    assert [c.plugin_ref.distribution for c in result.plugins] == ["untaped", "b-dist"]
    assert [(q.distribution, q.reason) for q in result.quarantine] == [("a-dist", reason)]

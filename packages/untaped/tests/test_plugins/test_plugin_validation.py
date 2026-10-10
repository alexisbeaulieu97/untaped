"""Row-by-row validation tests: every violation quarantines its provider.

Each rejection row becomes a ``QuarantineRecord`` naming the reason while
composition continues; first-party and third-party providers are judged alike.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from test_plugins.plugin_harness import (
    make_candidate,
    make_check,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.errors import ConfigError
from untaped.plugins.registry import (
    ApplicationSpec,
    DoctorCheck,
    PluginSpec,
    ProviderCandidate,
    SkillAsset,
    compose,
)


class TokenProfile(BaseModel):
    token: str = "t"
    other: str = "o"


class TokenState(BaseModel):
    token: str = ""


class EndpointState(BaseModel):
    endpoint: str = ""


def broken_asset(name: str = "", description: str = "d") -> SkillAsset:
    asset = object.__new__(SkillAsset)
    object.__setattr__(asset, "name", name)
    object.__setattr__(asset, "source", Path("/tmp/broken"))
    object.__setattr__(asset, "description", description)
    return asset


def _with(spec: PluginSpec, **fields: Any) -> PluginSpec:
    """Bypass construction checks the way a hostile provider could."""
    for key, value in fields.items():
        object.__setattr__(spec, key, value)
    return spec


def _check_with(**fields: Any) -> DoctorCheck:
    check = make_check("d.ok")
    for key, value in fields.items():
        object.__setattr__(check, key, value)
    return check


def _failing_factory() -> Any:
    raise RuntimeError("factory-boom")


#: One name per reason it is reserved: a plugin name, a config section and
#: a command group; the grammar already refuses ``format_version``.
RESERVED = [
    "untaped",
    "core",
    "sdk",
    "contracts",
    "plugins",
    "extensions",
    "profiles",
    "default",
    "shell",
    "http",
    "ui",
    "skills",
    "active",
    "config",
    "profile",
    "doctor",
    "setup",
    "auth",
    "alias",
    "plugin",
    "caches",
    "extensions",
]

# (spec factory, reason, text the error/detail must name)
SINGLE_SPEC_ROWS: list[tuple[str, Callable[[], PluginSpec], str, str]] = [
    *((f"reserved-name-{v}", lambda v=v: make_spec(name=v), "reserved-name", v) for v in RESERVED),
    (
        "settings-state-overlap",
        lambda: make_spec(name="o", settings=TokenProfile, state=TokenState),
        "settings-state-overlap",
        "token",
    ),
    (
        "skill-dup-in-spec",
        lambda: make_spec(name="a", skills=(make_skill("dup"), make_skill("dup"))),
        "duplicate-skill",
        "dup",
    ),
    (
        "skill-not-asset",
        lambda: _with(make_spec(name="bad-assets"), skills=("not-an-asset",)),
        "bad-skill-asset",
        "not-an-asset",
    ),
    (
        "skill-empty-name",
        lambda: _with(make_spec(name="bad-assets"), skills=(broken_asset(name="  "),)),
        "bad-skill-asset",
        "bad-assets",
    ),
    (
        "skill-empty-description",
        lambda: _with(
            make_spec(name="bad-assets"), skills=(broken_asset(name="x", description="  "),)
        ),
        "bad-skill-asset",
        "bad-assets",
    ),
    (
        "doctor-dup-in-spec",
        lambda: make_spec(name="d", checks=(make_check("d.x"), make_check("d.x"))),
        "duplicate-doctor-check",
        "d.x",
    ),
    (
        "doctor-empty-id",
        lambda: _with(make_spec(name="d"), doctor_checks=(_check_with(id="  "),)),
        "doctor-check-failed",
        "d",
    ),
    (
        "doctor-empty-title",
        lambda: make_spec(name="d", checks=(_check_with(title="  "),)),
        "doctor-check-failed",
        "d",
    ),
    (
        "doctor-non-callable",
        lambda: make_spec(name="d", checks=(_check_with(run=None),)),
        "doctor-check-failed",
        "d",
    ),
    *(
        (
            f"factory-{label}",
            lambda label=label, factory=factory: make_spec(
                name=f"factory-{label}", factory=factory
            ),
            "bad-app-factory",
            f"factory-{label}",
        )
        for label, factory in (
            ("takes-args", lambda arg: None),
            ("returns-str", lambda: "not-an-app"),
            ("raises", _failing_factory),
        )
    ),
]


@pytest.mark.parametrize(
    ("make", "reason", "named"),
    [row[1:] for row in SINGLE_SPEC_ROWS],
    ids=[row[0] for row in SINGLE_SPEC_ROWS],
)
def test_invalid_spec_is_quarantined(
    make: Callable[[], PluginSpec], reason: str, named: str
) -> None:
    spec = make()
    result = compose(make_shell(), [make_candidate(spec, "ext-dist")])
    assert result.plugins == ()
    (record,) = result.quarantine
    assert (record.distribution, record.reason) == ("ext-dist", reason)
    assert named in record.detail


# (first spec, colliding spec, reason, text the error/detail must name)
COLLISION_ROWS: list[tuple[str, Callable[[], tuple[PluginSpec, PluginSpec]], str, str]] = [
    (
        "skill",
        lambda: (
            make_spec(name="a", skills=(make_skill("shared"),)),
            make_spec(name="b", skills=(make_skill("shared"),)),
        ),
        "duplicate-skill",
        "'shared'",
    ),
    (
        "doctor-id",
        lambda: (
            make_spec(name="a", checks=(make_check("shared.id"),)),
            make_spec(name="b", checks=(make_check("shared.id"),)),
        ),
        "duplicate-doctor-check",
        "'shared.id'",
    ),
]


@pytest.mark.parametrize(
    ("make", "reason", "named"),
    [row[1:] for row in COLLISION_ROWS],
    ids=[row[0] for row in COLLISION_ROWS],
)
def test_collision_with_an_earlier_plugin_is_quarantined(
    make: Callable[[], tuple[PluginSpec, PluginSpec]],
    reason: str,
    named: str,
) -> None:
    first, second = make()
    result = compose(
        make_shell(), [make_candidate(second, "b-dist"), make_candidate(first, "a-dist")]
    )
    assert [c.spec.name for c in result.plugins] == [first.name]
    (record,) = result.quarantine
    assert (record.distribution, record.reason) == ("b-dist", reason)
    assert named in record.detail


@pytest.mark.parametrize(
    ("shell", "spec", "reason", "detail"),
    [
        (
            make_shell(name="root"),
            make_spec(name="root"),
            "duplicate-name",
            "duplicate plugin name: 'root' (already provided by the shell)",
        ),
        (
            make_shell(section="root-section"),
            make_spec(name="root-section"),
            "duplicate-name",
            "duplicate plugin name: 'root-section' (already provided by the shell)",
        ),
        (
            make_shell(skills=(make_skill("shell-skill"),)),
            make_spec(name="s", skills=(make_skill("shell-skill"),)),
            "duplicate-skill",
            "duplicate skill name: 'shell-skill'",
        ),
        (
            make_shell(checks=(make_check("shell.health"),)),
            make_spec(name="d", checks=(make_check("shell.health"),)),
            "duplicate-doctor-check",
            "duplicate doctor id: 'shell.health'",
        ),
    ],
    ids=["name", "section", "skill", "doctor-id"],
)
def test_collision_with_the_shell_quarantines(
    shell: Any, spec: PluginSpec, reason: str, detail: str
) -> None:
    result = compose(shell, [make_candidate(spec)])
    assert result.plugins == ()
    (record,) = result.quarantine
    assert (record.reason, record.detail) == (reason, detail)


def test_duplicate_skill_across_candidates_keeps_the_first() -> None:
    first = make_candidate(make_spec(name="a", skills=(make_skill("s1"),)), "d1")
    second = make_candidate(make_spec(name="b", skills=(make_skill("s1"),)), "d2")
    result = compose(make_shell(), [first, second])
    assert [c.spec.name for c in result.plugins] == ["a"]
    (record,) = result.quarantine
    assert record.reason == "duplicate-skill"


def test_a_plain_function_provider_composes() -> None:
    """The provider contract is a nullary callable; nothing else is declared."""
    spec = make_spec(name="plain")

    def provide() -> PluginSpec:
        return spec

    candidate = ProviderCandidate(distribution="plain-dist", name="plain", target=provide)
    result = compose(make_shell(), [candidate])
    assert result.quarantine == ()
    assert [registered.spec for registered in result.plugins] == [spec]


# ---- entry-point targets ------------------------------------------------------


def _needs_arg(value: str) -> PluginSpec:
    return make_spec(name="argful")


@pytest.mark.parametrize(
    ("candidate", "reason", "entry_point", "named"),
    [
        # An unimportable target has no resolved label; the detail names it.
        (
            ProviderCandidate(distribution="d", name="ghost", target="missing_mod_xyz:provider"),
            "malformed-entry-point",
            "",
            "missing_mod_xyz:provider",
        ),
        (
            ProviderCandidate(distribution="d", name="ghost", target="not-a-module-ref"),
            "malformed-entry-point",
            "",
            "",
        ),
        (
            ProviderCandidate(distribution="d", name="mod", target="json:decoder"),
            "malformed-entry-point",
            "json:decoder",
            "",
        ),
        # A dotted attribute resolves and is then judged on what it returns.
        (
            ProviderCandidate(distribution="d", name="jsoncap", target="json.decoder:JSONDecoder"),
            "malformed-entry-point",
            "json.decoder:JSONDecoder",
            "",
        ),
        (
            ProviderCandidate(distribution="d", name="thing", target=object()),
            "malformed-entry-point",
            "thing",
            "",
        ),
        (
            ProviderCandidate(distribution="d", name="argful", target=_needs_arg),
            "malformed-entry-point",
            "argful",
            "",
        ),
        (
            make_candidate(make_spec(name="raiser"), "d", error=RuntimeError("boom-text")),
            "malformed-entry-point",
            None,
            "boom-text",
        ),
        (
            make_candidate(make_spec(name="wrong"), "d", result={"not": "a-spec"}),
            "malformed-entry-point",
            None,
            "dict",
        ),
    ],
    ids=[
        "unresolvable",
        "no-colon",
        "non-callable-attr",
        "dotted-attr",
        "non-callable-object",
        "requires-arguments",
        "raises",
        "returns-non-spec",
    ],
)
def test_bad_entry_point_target_quarantines(
    candidate: ProviderCandidate, reason: str, entry_point: str | None, named: str
) -> None:
    result = compose(make_shell(), [candidate])
    (record,) = result.quarantine
    assert record.reason == reason
    assert record.distribution == "d"
    if entry_point is not None:
        assert record.entry_point == entry_point
    assert named in record.detail


class BadKeysProfile(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old": "nowhere"}
    value: int = 1


def test_broken_key_declarations_quarantine_only_that_plugin() -> None:
    bad = make_spec(name="bad", settings=BadKeysProfile)
    good = make_spec(name="good", settings=TokenProfile)

    result = compose(make_shell(), [make_candidate(bad), make_candidate(good)])

    assert [c.spec.name for c in result.plugins] == ["good"]
    (record,) = result.quarantine
    assert (record.reason, record.detail) == (
        "bad-settings-keys",
        "plugin 'bad': renamed key 'old' points at 'nowhere', which is not a setting",
    )


def _unset_variable() -> str:
    raise KeyError("NOPE")


class LazyProfile(BaseModel):
    renamed_keys: ClassVar[dict[str, str]] = {"old_dir": "cache_dir"}
    cache_dir: str = Field(default_factory=_unset_variable)


def test_a_raising_default_factory_is_not_called_while_composing() -> None:
    lazy = make_spec(name="lazy", settings=LazyProfile)
    good = make_spec(name="good", settings=TokenProfile)

    result = compose(make_shell(), [make_candidate(lazy), make_candidate(good)])

    assert ([c.spec.name for c in result.plugins], result.quarantine) == (["good", "lazy"], ())


def test_a_shell_with_broken_key_declarations_fails_loudly() -> None:
    with pytest.raises(ConfigError, match="invalid key declarations on BadKeysProfile"):
        ApplicationSpec(
            name="untaped",
            app_factory=lambda: App(),
            section="shell",
            settings=BadKeysProfile,
        )

"""Row-by-row validation tests: every violation quarantines its provider (spec §5).

Each rejection row becomes a ``QuarantineRecord`` naming the reason while
composition continues; first-party and third-party providers are judged alike.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from test_capabilities.capharness import (
    OtherProfile,
    make_candidate,
    make_check,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.capabilities.registry import (
    CapabilitySpec,
    DoctorCheck,
    ProviderCandidate,
    SkillAsset,
    compose,
)


class TokenProfile(BaseModel):
    token: str = "t"
    other: str = "o"


class TokenState(BaseModel):
    token: str = ""


def broken_asset(name: str = "", description: str = "d") -> SkillAsset:
    asset = object.__new__(SkillAsset)
    object.__setattr__(asset, "name", name)
    object.__setattr__(asset, "source", Path("/tmp/broken"))
    object.__setattr__(asset, "description", description)
    return asset


def _with(spec: CapabilitySpec, **fields: Any) -> CapabilitySpec:
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


RESERVED = [
    "http",
    "ui",
    "profiles",
    "active",
    "config",
    "profile",
    "skills",
    "doctor",
    "capabilities",
]

# (spec factory, reason, text the error/detail must name)
SINGLE_SPEC_ROWS: list[tuple[str, Callable[[], CapabilitySpec], str, str]] = [
    *(
        (f"reserved-name-{v}", lambda v=v: make_spec(name=v, section=f"ok-{v}"), "reserved-root", v)
        for v in RESERVED
    ),
    *(
        (
            f"reserved-section-{v}",
            lambda v=v: make_spec(name=f"ok-{v}", section=v),
            "reserved-root",
            v,
        )
        for v in RESERVED
    ),
    (
        "profile-state-overlap",
        lambda: make_spec(name="o", profile=TokenProfile, state=TokenState),
        "profile-state-overlap",
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
    make: Callable[[], CapabilitySpec], reason: str, named: str
) -> None:
    spec = make()
    result = compose(make_shell(), [make_candidate(spec, "ext-dist")])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert (record.distribution, record.reason) == ("ext-dist", reason)
    assert named in record.detail


# (first spec, colliding spec, reason, text the error/detail must name)
COLLISION_ROWS: list[tuple[str, Callable[[], tuple[CapabilitySpec, CapabilitySpec]], str, str]] = [
    (
        "name",
        lambda: (make_spec(name="taken"), make_spec(name="taken", profile=OtherProfile)),
        "duplicate-name",
        "'taken'",
    ),
    (
        "section",
        lambda: (make_spec(name="a", section="shared"), make_spec(name="b", section="shared")),
        "duplicate-section",
        "'shared'",
    ),
    (
        "state-shadow",
        lambda: (
            make_spec(name="first", section="data", profile=TokenProfile),
            make_spec(name="second", section="data", profile=OtherProfile, state=TokenState),
        ),
        "state-shadow",
        "'data'",
    ),
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
def test_collision_with_an_earlier_capability_is_quarantined(
    make: Callable[[], tuple[CapabilitySpec, CapabilitySpec]],
    reason: str,
    named: str,
) -> None:
    first, second = make()
    result = compose(
        make_shell(), [make_candidate(second, "b-dist"), make_candidate(first, "a-dist")]
    )
    assert [c.spec.name for c in result.capabilities] == [first.name]
    (record,) = result.quarantine
    assert (record.distribution, record.reason) == ("b-dist", reason)
    assert named in record.detail


@pytest.mark.parametrize(
    ("shell", "spec", "reason", "detail"),
    [
        (
            make_shell(),
            make_spec(name="untaped"),
            "duplicate-name",
            "duplicate capability name: 'untaped' (already provided by the shell)",
        ),
        (
            make_shell(),
            make_spec(name="intruder", section="shell"),
            "duplicate-section",
            "duplicate config section: 'shell' (already provided by the shell)",
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
    shell: Any, spec: CapabilitySpec, reason: str, detail: str
) -> None:
    result = compose(shell, [make_candidate(spec)])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert (record.reason, record.detail) == (reason, detail)


def test_state_shadow_scoped_to_same_section() -> None:
    first = make_spec(name="first", section="data", profile=TokenProfile)
    other = make_spec(name="other", section="other", profile=OtherProfile, state=TokenState)
    result = compose(make_shell(), [make_candidate(first), make_candidate(other)])
    assert [c.spec.name for c in result.capabilities] == ["first", "other"]
    assert result.quarantine == ()


def test_duplicate_skill_across_candidates_keeps_the_first() -> None:
    first = make_candidate(make_spec(name="a", skills=(make_skill("s1"),)), "d1")
    second = make_candidate(make_spec(name="b", skills=(make_skill("s1"),)), "d2")
    result = compose(make_shell(), [first, second])
    assert [c.spec.name for c in result.capabilities] == ["a"]
    (record,) = result.quarantine
    assert record.reason == "duplicate-skill"


def test_a_plain_function_provider_composes() -> None:
    """The provider contract is a nullary callable; nothing else is declared."""
    spec = make_spec(name="plain")

    def provide() -> CapabilitySpec:
        return spec

    candidate = ProviderCandidate(distribution="plain-dist", name="plain", target=provide)
    result = compose(make_shell(), [candidate])
    assert result.quarantine == ()
    assert [registered.spec for registered in result.capabilities] == [spec]


# ---- entry-point targets ------------------------------------------------------


def _needs_arg(value: str) -> CapabilitySpec:
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

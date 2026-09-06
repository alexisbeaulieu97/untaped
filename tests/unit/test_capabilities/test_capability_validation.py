"""Row-by-row validation tests: fatal built-ins, quarantined externals (spec §5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

import untaped.capabilities.registry as registry
from test_capabilities.capharness import (
    OtherProfile,
    function_provider,
    make_check,
    make_external,
    make_shell,
    make_skill,
    make_spec,
)
from untaped.capabilities.registry import (
    CapabilitySpec,
    DoctorCheck,
    ExternalProvider,
    ProviderRef,
    SkillAsset,
    check_api_range,
    check_builtin_metadata,
    compose,
)
from untaped.errors import ConfigError


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


def fatal_spec(spec: CapabilitySpec) -> ConfigError:
    with pytest.raises(ConfigError) as exc_info:
        compose(make_shell(), [spec])
    return exc_info.value


def quarantine_reason(
    spec: CapabilitySpec,
    distribution: str = "ext-dist",
    name: str | None = None,
    **kwargs: Any,
) -> Any:
    result = compose(make_shell(), [], [make_external(spec, distribution, name, **kwargs)])
    assert len(result.capabilities) == 0
    (record,) = result.quarantine
    return record


RESERVED = [
    "log_level",
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


@pytest.mark.parametrize("value", RESERVED)
def test_reserved_name_builtin_fatal(value: str) -> None:
    err = fatal_spec(make_spec(name=value, section=f"ok-{value}"))
    assert "reserved-root" in str(err)
    assert value in str(err)


@pytest.mark.parametrize("value", RESERVED)
def test_reserved_section_builtin_fatal(value: str) -> None:
    err = fatal_spec(make_spec(name=f"ok-{value}", section=value))
    assert "reserved-root" in str(err)
    assert value in str(err)


@pytest.mark.parametrize("value", RESERVED)
def test_reserved_name_external_quarantine(value: str) -> None:
    record = quarantine_reason(make_spec(name=value, section=f"ok-{value}"))
    assert record.reason == "reserved-root"
    assert value in record.detail


@pytest.mark.parametrize("value", RESERVED)
def test_reserved_section_external_quarantine(value: str) -> None:
    record = quarantine_reason(make_spec(name=f"ok-{value}", section=value))
    assert record.reason == "reserved-root"
    assert value in record.detail


def test_duplicate_name_builtin_fatal() -> None:
    with pytest.raises(ConfigError, match="duplicate-name"):
        compose(make_shell(), [make_spec(name="a"), make_spec(name="a", profile=OtherProfile)])


def test_duplicate_name_external_quarantine() -> None:
    clash = compose(
        make_shell(),
        [make_spec(name="taken")],
        [make_external(make_spec(name="taken", profile=OtherProfile))],
    )
    assert [c.spec.name for c in clash.capabilities] == ["taken"]
    (record,) = clash.quarantine
    assert record.reason == "duplicate-name"
    assert "'taken'" in record.detail


def test_shell_name_collision_quarantines() -> None:
    record = quarantine_reason(make_spec(name="untaped"))
    assert record.reason == "duplicate-name"
    assert "'untaped'" in record.detail


def test_duplicate_section_builtin_fatal() -> None:
    with pytest.raises(ConfigError, match="duplicate-section"):
        compose(
            make_shell(),
            [make_spec(name="a", section="shared"), make_spec(name="b", section="shared")],
        )


def test_duplicate_section_external_quarantine() -> None:
    result = compose(
        make_shell(),
        [make_spec(name="a", section="shared")],
        [make_external(make_spec(name="b", section="shared"))],
    )
    assert [c.spec.name for c in result.capabilities] == ["a"]
    (record,) = result.quarantine
    assert record.reason == "duplicate-section"
    assert "'shared'" in record.detail


def test_shell_section_collision_quarantines() -> None:
    record = quarantine_reason(make_spec(name="intruder", section="shell"))
    assert record.reason == "duplicate-section"
    assert "'shell'" in record.detail


def test_profile_state_overlap_builtin_fatal() -> None:
    err = fatal_spec(make_spec(name="o", profile=TokenProfile, state=TokenState))
    assert "profile-state-overlap" in str(err)
    assert "token" in str(err)


def test_profile_state_overlap_external_quarantine() -> None:
    record = quarantine_reason(make_spec(name="o", profile=TokenProfile, state=TokenState))
    assert record.reason == "profile-state-overlap"
    assert "token" in record.detail


def test_state_shadow_external_quarantine() -> None:
    first = make_spec(name="first", section="data", profile=TokenProfile)
    second = make_spec(name="second", section="data", profile=OtherProfile, state=TokenState)
    result = compose(make_shell(), [first], [make_external(second)])
    assert [c.spec.name for c in result.capabilities] == ["first"]
    (record,) = result.quarantine
    assert record.reason == "state-shadow"
    assert "token" in record.detail
    assert "'data'" in record.detail


def test_state_shadow_builtin_fatal() -> None:
    first = make_spec(name="first", section="data", profile=TokenProfile)
    second = make_spec(name="second", section="data", profile=OtherProfile, state=TokenState)
    with pytest.raises(ConfigError, match="state-shadow"):
        compose(make_shell(), [first, second])


def test_state_shadow_scoped_to_same_section() -> None:
    first = make_spec(name="first", section="data", profile=TokenProfile)
    other = make_spec(name="other", section="other", profile=OtherProfile, state=TokenState)
    result = compose(make_shell(), [first], [make_external(other)])
    assert [c.spec.name for c in result.capabilities] == ["first", "other"]
    assert result.quarantine == ()


def test_duplicate_skill_builtin_fatal() -> None:
    first = make_spec(name="a", skills=(make_skill("shared"),))
    second = make_spec(name="b", skills=(make_skill("shared"),))
    with pytest.raises(ConfigError, match="duplicate-skill"):
        compose(make_shell(), [first, second])


def test_duplicate_skill_within_spec_fatal() -> None:
    err = fatal_spec(make_spec(name="a", skills=(make_skill("dup"), make_skill("dup"))))
    assert "duplicate-skill" in str(err)


def test_duplicate_skill_external_quarantine() -> None:
    shell = make_shell(skills=(make_skill("shell-skill"),))
    candidate = make_external(make_spec(name="s", skills=(make_skill("shell-skill"),)))
    result = compose(shell, [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "duplicate-skill"
    assert "'shell-skill'" in record.detail


def test_duplicate_skill_across_externals() -> None:
    first = make_external(make_spec(name="a", skills=(make_skill("s1"),)), "d1")
    second = make_external(make_spec(name="b", skills=(make_skill("s1"),)), "d2")
    result = compose(make_shell(), [], [first, second])
    assert [c.spec.name for c in result.capabilities] == ["a"]
    (record,) = result.quarantine
    assert record.reason == "duplicate-skill"


def test_bad_skill_asset_external_quarantine() -> None:
    spec = make_spec(name="bad-assets")
    object.__setattr__(spec, "skills", ("not-an-asset",))
    record = quarantine_reason(spec)
    assert record.reason == "bad-skill-asset"
    assert "not-an-asset" in record.detail


def test_bad_skill_asset_empty_name_quarantine() -> None:
    spec = make_spec(name="bad-assets")
    object.__setattr__(spec, "skills", (broken_asset(name="  "),))
    record = quarantine_reason(spec)
    assert record.reason == "bad-skill-asset"


def test_bad_skill_asset_empty_description_quarantine() -> None:
    spec = make_spec(name="bad-assets")
    object.__setattr__(spec, "skills", (broken_asset(name="x", description="  "),))
    record = quarantine_reason(spec)
    assert record.reason == "bad-skill-asset"


def test_bad_skill_asset_builtin_fatal() -> None:
    spec = make_spec(name="bad-assets")
    object.__setattr__(spec, "skills", ("not-an-asset",))
    err = fatal_spec(spec)
    assert "bad-skill-asset" in str(err)


def test_duplicate_doctor_id_builtin_fatal() -> None:
    first = make_spec(name="a", checks=(make_check("shared.id"),))
    second = make_spec(name="b", checks=(make_check("shared.id"),))
    with pytest.raises(ConfigError, match="duplicate-doctor-check"):
        compose(make_shell(), [first, second])


def test_duplicate_doctor_id_external_quarantine() -> None:
    shell = make_shell(checks=(make_check("shell.health"),))
    spec = make_spec(name="d", checks=(make_check("shell.health"),))
    result = compose(shell, [], [make_external(spec)])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "duplicate-doctor-check"
    assert "'shell.health'" in record.detail


def test_doctor_empty_id_quarantine() -> None:
    spec = make_spec(name="d")
    object.__setattr__(
        spec, "doctor_checks", (DoctorCheck(id="  ", title="T", run=lambda ctx: None),)
    )
    record = quarantine_reason(spec)
    assert record.reason == "duplicate-doctor-check"


def test_doctor_empty_title_quarantine() -> None:
    check = make_check("d.ok")
    object.__setattr__(check, "title", "  ")
    spec = make_spec(name="d", checks=(check,))
    record = quarantine_reason(spec)
    assert record.reason == "duplicate-doctor-check"


def test_doctor_non_callable_body_quarantine() -> None:
    check = make_check("d.ok")
    object.__setattr__(check, "run", None)
    spec = make_spec(name="d", checks=(check,))
    record = quarantine_reason(spec)
    assert record.reason == "duplicate-doctor-check"


def test_doctor_within_spec_duplicate_fatal() -> None:
    err = fatal_spec(make_spec(name="d", checks=(make_check("d.x"), make_check("d.x"))))
    assert "duplicate-doctor-check" in str(err)


GOOD_RANGES = [
    ((1.0, 2.0), (1.0, 2.0)),
    ((0.0, 99.0), (0.0, 99.0)),
    ((1.0, 1.5), (1.0, 1.5)),
    ([1.0, 2.0], (1.0, 2.0)),
    ((1, 2), (1.0, 2.0)),
]


@pytest.mark.parametrize(("rng", "expected"), GOOD_RANGES)
def test_api_range_accepts_covering_ranges(rng: Any, expected: Any) -> None:
    assert check_api_range(rng, 1.0) == expected


@pytest.mark.parametrize(("rng", "expected"), GOOD_RANGES)
def test_api_range_covering_ranges_compose(rng: Any, expected: Any) -> None:
    spec = make_spec(name="ranged")
    result = compose(make_shell(), [], [make_external(spec, api_requires=rng)])
    assert [c.spec.name for c in result.capabilities] == ["ranged"]
    assert result.quarantine == ()


BAD_RANGES = [
    ((1.0, 1.0), "inverted"),
    ((2.0, 1.0), "inverted"),
    (("1.0", 2.0), "non-numeric"),
    ((1.0,), "pair"),
    ((1.0, 2.0, 3.0), "pair"),
    ("1.0", "pair"),
    ({"lo": 1.0}, "pair"),
    ((True, 2.0), "non-numeric"),
    ((float("nan"), 2.0), "non-numeric"),
    ((1.0, float("inf")), "non-finite"),
    ((0.5, 1.0), "admit"),
    ((1.5, 2.0), "admit"),
    ((0.5, 0.9), "admit"),
    (None, "missing"),
]


@pytest.mark.parametrize(("rng", "kind"), BAD_RANGES)
def test_api_range_rejects_bad_ranges(rng: Any, kind: str) -> None:
    with pytest.raises(ConfigError, match="api-range"):
        check_api_range(rng, 1.0)


@pytest.mark.parametrize(("rng", "kind"), BAD_RANGES)
def test_api_range_external_quarantine(rng: Any, kind: str) -> None:
    spec = make_spec(name="ranged")
    result = compose(make_shell(), [], [make_external(spec, api_requires=rng)])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "api-range"
    assert record.detail


def test_api_range_missing_attribute_quarantine() -> None:
    spec = make_spec(name="bare")

    def _bare() -> CapabilitySpec:
        return spec

    candidate = ExternalProvider(distribution="bare-dist", name="bare", target=_bare)
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "api-range"


def test_api_range_boundary_lo_inclusive() -> None:
    lo, _hi = check_api_range((1.0, 1.0 + 1e-9), 1.0)
    assert lo == 1.0


def test_api_range_builtin_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(registry, "CAPABILITY_API_VERSION", 5.0)
    with pytest.raises(ConfigError, match="api-range"):
        compose(make_shell(), [make_spec(name="built")])


def test_malformed_unresolvable_target() -> None:
    candidate = ExternalProvider(
        distribution="ghost", name="ghost", target="missing_mod_xyz:provider"
    )
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert record.entry_point == ""
    assert "missing_mod_xyz:provider" in record.detail


def test_malformed_target_without_colon() -> None:
    candidate = ExternalProvider(distribution="ghost", name="ghost", target="not-a-module-ref")
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert record.entry_point == ""


def test_resolved_non_callable_keeps_target_label() -> None:
    candidate = ExternalProvider(distribution="mod-dist", name="mod", target="json:decoder")
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert record.entry_point == "json:decoder"
    assert record.distribution == "mod-dist"


def test_dotted_attr_target_resolves() -> None:
    candidate = ExternalProvider(
        distribution="json-dist", name="jsoncap", target="json.decoder:JSONDecoder"
    )
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "api-range"
    assert record.entry_point == "json.decoder:JSONDecoder"


def test_malformed_non_callable_target() -> None:
    candidate = ExternalProvider(distribution="d", name="thing", target=object())
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert record.entry_point == "thing"


def test_malformed_provider_requiring_arguments() -> None:
    spec = make_spec(name="argful")

    def _needs_arg(value: str) -> CapabilitySpec:
        return spec

    _needs_arg.api_requires = (1.0, 2.0)  # type: ignore[attr-defined]
    candidate = ExternalProvider(distribution="d", name="argful", target=_needs_arg)
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"


def test_malformed_provider_raising() -> None:
    spec = make_spec(name="raiser")
    candidate = make_external(spec, error=RuntimeError("boom-text"))
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert "boom-text" in record.detail


def test_malformed_provider_returning_non_spec() -> None:
    spec = make_spec(name="wrong")
    candidate = make_external(spec, result={"not": "a-spec"})
    result = compose(make_shell(), [], [candidate])
    (record,) = result.quarantine
    assert record.reason == "malformed-entry-point"
    assert "dict" in record.detail


def test_bad_factory_variants_external() -> None:
    variants = {
        "takes-args": (lambda arg: None),
        "returns-str": (lambda: "not-an-app"),
        "raises": (_failing_factory),
    }
    for label, factory in variants.items():
        spec = make_spec(name=f"factory-{label}", factory=factory)  # type: ignore[arg-type]
        record = quarantine_reason(spec)
        assert record.reason == "bad-app-factory", label
        assert f"factory-{label}" in record.detail


def _failing_factory() -> Any:
    raise RuntimeError("factory-boom")


def test_bad_factory_builtin_fatal() -> None:
    spec = make_spec(name="bad", factory=lambda: "not-an-app")  # type: ignore[arg-type]
    err = fatal_spec(spec)
    assert "bad-app-factory" in str(err)


def test_bad_factory_takes_args_builtin_fatal() -> None:
    spec = make_spec(name="bad", factory=lambda arg: None)  # type: ignore[arg-type]
    err = fatal_spec(spec)
    assert "bad-app-factory" in str(err)


def test_bad_metadata_name_mismatch() -> None:
    spec = make_spec(name="real")
    result = compose(make_shell(), [], [make_external(spec, "d", name="alias")])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert "'alias'" in record.detail
    assert "'real'" in record.detail


def test_bad_metadata_empty_distribution() -> None:
    spec = make_spec(name="nodist")
    candidate = ExternalProvider(distribution="  ", name="nodist", target=function_provider(spec))
    result = compose(make_shell(), [], [candidate])
    assert result.capabilities == ()
    (record,) = result.quarantine
    assert record.reason == "bad-metadata"
    assert record.distribution == "unknown"


def test_builtin_metadata_helper_accepts_valid_ref() -> None:
    check_builtin_metadata(
        ProviderRef(
            kind="built-in", distribution="untaped", entry_point="", api_requires=(1.0, 2.0)
        )
    )


def test_builtin_metadata_helper_rejects_bad_ref() -> None:
    with pytest.raises(ConfigError, match="bad-metadata"):
        check_builtin_metadata(
            ProviderRef(
                kind="external",
                distribution="evil",
                entry_point="m:a",
                api_requires=(1.0, 2.0),
            )
        )

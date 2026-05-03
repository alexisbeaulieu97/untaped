"""ResolveCasePayload: merge defaults ⤥ case, resolve FKs + !ref, warn on typos."""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from untaped_awx.application.test.resolver import (
    KNOWN_LAUNCH_FIELDS,
    ResolveCasePayload,
    UnknownLaunchFieldWarning,
)
from untaped_awx.domain.test_suite import Case, RefSentinel
from untaped_awx.errors import ResourceNotFound
from untaped_awx.infrastructure.specs import JOB_TEMPLATE_SPEC


class StubFkResolver:
    """Records lookups; returns a fixed mapping or raises ResourceNotFound."""

    def __init__(self, mapping: dict[tuple[str, str], int] | None = None) -> None:
        self._map = mapping or {}
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        self.calls.append((kind, name, dict(scope) if scope else None))
        if (kind, name) not in self._map:
            raise ResourceNotFound(kind, {"name": name})
        return self._map[(kind, name)]


def _resolve(case_body: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    fk = kwargs.pop("fk", StubFkResolver())
    defaults_body = kwargs.pop("defaults", None)
    case = Case.model_validate(case_body)
    defaults = Case.model_validate(defaults_body) if defaults_body is not None else None
    resolver = ResolveCasePayload(fk, default_organization=kwargs.pop("default_org", None))
    return resolver(JOB_TEMPLATE_SPEC, case, defaults=defaults)


# ---- merge ---------------------------------------------------------------


def test_extra_vars_deep_merge_case_wins() -> None:
    payload = _resolve(
        {"launch": {"extra_vars": {"region": "us-east-1", "log_level": "debug"}}},
        defaults={"launch": {"extra_vars": {"log_level": "info", "tag": "main"}}},
    )
    assert payload["extra_vars"] == {
        "region": "us-east-1",
        "log_level": "debug",
        "tag": "main",
    }


def test_labels_concat_and_dedupe() -> None:
    payload = _resolve(
        {"launch": {"labels": [10, 20]}},
        defaults={"launch": {"labels": [20, 30]}},
        fk=StubFkResolver(),
    )
    assert payload["labels"] == [20, 30, 10]  # defaults first, then case extras


def test_other_fields_case_overrides() -> None:
    payload = _resolve(
        {"launch": {"limit": "case-only"}},
        defaults={"launch": {"limit": "defaults-value", "verbosity": 1}},
    )
    assert payload["limit"] == "case-only"
    assert payload["verbosity"] == 1


# ---- FK resolution -------------------------------------------------------


def test_inventory_string_resolved_to_id() -> None:
    fk = StubFkResolver({("Inventory", "Web Inventory"): 7})
    payload = _resolve({"launch": {"inventory": "Web Inventory"}}, fk=fk)
    assert payload["inventory"] == 7
    assert fk.calls[0][0:2] == ("Inventory", "Web Inventory")


def test_inventory_int_passes_through() -> None:
    fk = StubFkResolver()
    payload = _resolve({"launch": {"inventory": 7}}, fk=fk)
    assert payload["inventory"] == 7
    assert fk.calls == []  # int pre-resolved; never queried


def test_credentials_mixed_list() -> None:
    fk = StubFkResolver({("Credential", "github-pat"): 42})
    payload = _resolve(
        {"launch": {"credentials": ["github-pat", 99]}},
        fk=fk,
    )
    assert payload["credentials"] == [42, 99]


def test_launch_only_fk_labels() -> None:
    fk = StubFkResolver({("Label", "smoke"): 5, ("Label", "slow"): 6})
    payload = _resolve({"launch": {"labels": ["smoke", "slow"]}}, fk=fk)
    assert payload["labels"] == [5, 6]


def test_extra_vars_is_not_walked_for_fk() -> None:
    """``extra_vars`` is opaque — names inside should NOT be resolved."""
    fk = StubFkResolver()
    payload = _resolve(
        {"launch": {"extra_vars": {"some_name": "Web Inventory"}}},
        fk=fk,
    )
    assert payload["extra_vars"] == {"some_name": "Web Inventory"}
    assert fk.calls == []


# ---- !ref walked recursively --------------------------------------------


def test_ref_inside_extra_vars_resolved() -> None:
    fk = StubFkResolver({("Inventory", "Web"): 7})
    payload = _resolve(
        {
            "launch": {
                "extra_vars": {"inv_id": RefSentinel(kind="Inventory", name="Web")},
            }
        },
        fk=fk,
    )
    assert payload["extra_vars"]["inv_id"] == 7


def test_ref_in_top_level_fk_field() -> None:
    fk = StubFkResolver({("Credential", "github-pat"): 42})
    payload = _resolve(
        {"launch": {"credentials": [RefSentinel(kind="Credential", name="github-pat"), 99]}},
        fk=fk,
    )
    assert payload["credentials"] == [42, 99]


def test_user_dict_with_name_kind_keys_left_alone() -> None:
    """A bare dict shaped like a !ref but without the tag is opaque."""
    payload = _resolve(
        {"launch": {"extra_vars": {"user": {"name": "Alice", "kind": "admin"}}}},
    )
    assert payload["extra_vars"]["user"] == {"name": "Alice", "kind": "admin"}


# ---- unknown-field warnings ---------------------------------------------


def test_unknown_launch_field_emits_warning() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        payload = _resolve({"launch": {"frooks": 4}})
    assert payload["frooks"] == 4
    assert any(
        isinstance(w.message, UnknownLaunchFieldWarning) and "frooks" in str(w.message)
        for w in caught
    )


def test_known_field_emits_no_warning() -> None:
    """Sanity check: every documented field should be in the allowlist."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _resolve({"launch": {"extra_vars": {}, "limit": "x", "job_tags": "smoke"}})
    assert not any(isinstance(w.message, UnknownLaunchFieldWarning) for w in caught)


def test_known_launch_fields_includes_core_set() -> None:
    """Smoke test the allowlist isn't accidentally empty."""
    for field in (
        "extra_vars",
        "limit",
        "job_tags",
        "skip_tags",
        "job_type",
        "verbosity",
        "diff_mode",
        "credentials",
        "credential_passwords",
        "execution_environment",
        "forks",
        "timeout",
        "scm_branch",
        "labels",
        "instance_groups",
        "inventory",
    ):
        assert field in KNOWN_LAUNCH_FIELDS, field


# ---- defaults pass-through -----------------------------------------------


def test_no_defaults_no_overrides() -> None:
    payload = _resolve({"launch": {"limit": "x"}})
    assert payload == {"limit": "x"}


def test_empty_case_with_defaults_uses_defaults() -> None:
    payload = _resolve({"launch": {}}, defaults={"launch": {"limit": "default-x"}})
    assert payload == {"limit": "default-x"}


# ---- error wrapping -----------------------------------------------------


def test_unresolved_name_raises() -> None:
    fk = StubFkResolver()  # empty
    with pytest.raises(ResourceNotFound):
        _resolve({"launch": {"inventory": "missing"}}, fk=fk)

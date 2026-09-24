"""Unit tests for SecretPreservationPolicy.

After ``$encrypted$`` placeholders are stripped from the payload, the policy
decides which top-level fields can be omitted from the PATCH (AWX retains
them) and which carry a sibling change that would clobber the stored secret.
"""

from __future__ import annotations

from typing import Any

import pytest

from untaped.capabilities.awx.application.apply_secret_policy import SecretPreservationPolicy

_SURVEY = [
    {"variable": "v1", "default": "$encrypted$"},
    {"variable": "v2", "default": "$encrypted$"},
]


@pytest.mark.parametrize(
    ("payload", "existing", "preserved", "keep", "conflicts"),
    [
        # create: nothing to preserve (placeholders are refused separately)
        ({"name": "n"}, None, ["webhook_key"], set(), []),
        ({"description": "d"}, {"description": "old"}, [], set(), []),
        ({}, {"webhook_key": "secret"}, ["webhook_key"], {"webhook_key"}, []),
        # a sibling change under the same top-level key would clobber the secret
        ({"inputs": {"username": "new"}}, {"inputs": {"username": "old", "password": "s"}},
         ["inputs.password"], set(), ["inputs"]),
        ({"inputs": {"username": "u"}}, {"inputs": {"username": "u", "password": "s"}},
         ["inputs.password"], {"inputs"}, []),
        ({"inputs": {"endpoint": "e"}}, {"inputs": {"endpoint": "e", "user": "u", "key": "k"}},
         ["inputs.user", "inputs.key"], {"inputs"}, []),
        # list traversal is recorded as ``*`` and normalises both sides alike
        ({"survey_spec": {"spec": [{"variable": "v1"}, {"variable": "v2"}]}},
         {"survey_spec": {"spec": _SURVEY}}, ["survey_spec.spec.*.default"], {"survey_spec"}, []),
        # independent top-level keys are classified independently
        ({"inputs": {"u": "changed"}, "credential": {}},
         {"inputs": {"u": "u", "p": "s"}, "credential": {"token": "t"}},
         ["inputs.p", "credential.token"], {"credential"}, ["inputs"]),
    ],
)  # fmt: skip
def test_partition(
    payload: dict[str, Any],
    existing: dict[str, Any] | None,
    preserved: list[str],
    keep: set[str],
    conflicts: list[str],
) -> None:
    assert SecretPreservationPolicy().partition(
        write_payload=payload, existing=existing, preserved=preserved
    ) == (keep, conflicts)


@pytest.mark.parametrize(
    ("obj", "path", "expected"),
    [
        ({"inputs": {"user": "u", "password": "p"}}, "inputs.password", {"inputs": {"user": "u"}}),
        ({"inputs": {"a": 1, "b": 2}}, "inputs.*", {"inputs": {}}),
        ({"s": {"spec": [{"default": "x", "k": 1}, {"default": "y", "k": 2}]}}, "s.spec.*.default",
         {"s": {"spec": [{"k": 1}, {"k": 2}]}}),
        ({"a": {"user": "u1", "password": "p1"}, "b": {"password": "p2"}}, "*.password",
         {"a": {"user": "u1"}, "b": {}}),
        ([1, 2, 3], "*", []),
        # absent or wrongly shaped paths are no-ops, never errors
        ({"name": "n"}, "inputs.missing", {"name": "n"}),
        ({"a": None}, "a.k", {"a": None}),
        ({"a": "scalar"}, "a.k", {"a": "scalar"}),
        ({"a": 42}, "a.k.v", {"a": 42}),
    ],
)  # fmt: skip
def test_strip_paths(obj: Any, path: str, expected: Any) -> None:
    before = repr(obj)
    assert SecretPreservationPolicy.strip_paths(obj, [path]) == expected
    assert repr(obj) == before  # the input is never mutated

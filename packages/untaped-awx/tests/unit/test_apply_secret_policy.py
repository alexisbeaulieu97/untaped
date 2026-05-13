"""Unit tests for SecretPreservationPolicy.

The policy is the apply pipeline's *second* pass over secrets — after
``strip_encrypted_in_place`` has dropped ``$encrypted$`` placeholders from the
payload, the policy decides which top-level fields can safely be
omitted from the PATCH (AWX retains them) versus which carry a sibling
change that would clobber the existing secret if PATCHed.

Pure function-shaped behaviour: no I/O, no Protocols, no network. Tests
cover the partition contract directly.
"""

from __future__ import annotations

import pytest
from untaped_awx.application.apply_secret_policy import SecretPreservationPolicy


def test_partition_returns_empty_for_create_path() -> None:
    """``existing is None`` (create) means there's nothing to preserve;
    the caller's _do_create enforces no-placeholders separately."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"name": "n"},
        existing=None,
        preserved=["webhook_key"],
    )
    assert preserved_fields == set()
    assert conflict_fields == []


def test_partition_returns_empty_when_no_secrets_preserved() -> None:
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"name": "n", "description": "d"},
        existing={"name": "n", "description": "old"},
        preserved=[],
    )
    assert preserved_fields == set()
    assert conflict_fields == []


def test_partition_marks_field_preserved_when_no_sibling_change() -> None:
    """User stripped ``webhook_key`` (placeholder); no other top-level
    change. Body is empty post-strip, so the field is safe to omit
    from PATCH — AWX retains the existing secret."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={},  # webhook_key already stripped
        existing={"webhook_key": "secret-value"},
        preserved=["webhook_key"],
    )
    assert preserved_fields == {"webhook_key"}
    assert conflict_fields == []


def test_partition_flags_conflict_when_sibling_change_at_same_top_level() -> None:
    """User stripped ``inputs.password`` (placeholder) but also changed
    ``inputs.username``. PATCHing the new ``inputs`` body would clobber
    the password. Flag as conflict so the caller can refuse."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"inputs": {"username": "new-user"}},
        existing={"inputs": {"username": "old-user", "password": "secret"}},
        preserved=["inputs.password"],
    )
    assert preserved_fields == set()
    assert conflict_fields == ["inputs"]


def test_partition_preserves_inputs_when_only_secrets_changed() -> None:
    """If user stripped a secret and made no sibling change at the same
    top-level, that key is preserved (omit from PATCH)."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"inputs": {"username": "u"}},
        existing={"inputs": {"username": "u", "password": "secret"}},
        preserved=["inputs.password"],
    )
    assert preserved_fields == {"inputs"}
    assert conflict_fields == []


def test_partition_handles_per_path_secrets() -> None:
    """``preserved`` carries the *actual* paths matched by
    ``strip_encrypted_in_place`` (one entry per ``$encrypted$`` placeholder
    found), not the spec glob. With every secret path stripped from
    both sides, an unchanged-modulo-secrets payload is preserved."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"inputs": {"endpoint": "https://example.com"}},
        existing={
            "inputs": {
                "endpoint": "https://example.com",
                "user": "u",
                "pass": "s1",
                "key": "s2",
            }
        },
        preserved=["inputs.user", "inputs.pass", "inputs.key"],
    )
    assert preserved_fields == {"inputs"}
    assert conflict_fields == []


def test_partition_handles_list_glob_in_preserved_path() -> None:
    """``strip_encrypted_in_place`` records list traversal as ``*`` in the path
    (e.g. ``survey_spec.spec.*.default``). The policy's ``strip_paths``
    must apply the same glob shape so the comparison normalises both
    sides identically."""
    policy = SecretPreservationPolicy()
    desired = [{"variable": "v1"}, {"variable": "v2"}]
    existing_inputs = [
        {"variable": "v1", "default": "$encrypted$"},
        {"variable": "v2", "default": "$encrypted$"},
    ]
    preserved_fields, conflict_fields = policy.partition(
        write_payload={"survey_spec": {"spec": desired}},
        existing={"survey_spec": {"spec": existing_inputs}},
        preserved=["survey_spec.spec.*.default"],
    )
    assert preserved_fields == {"survey_spec"}
    assert conflict_fields == []


def test_partition_handles_multiple_independent_top_levels() -> None:
    """Two unrelated secret paths at different top-levels — each is
    classified independently."""
    policy = SecretPreservationPolicy()
    preserved_fields, conflict_fields = policy.partition(
        write_payload={
            "inputs": {"u": "u-changed"},  # sibling change → conflict
            "credential": {},  # only secrets touched → preserve
        },
        existing={
            "inputs": {"u": "u", "p": "secret"},
            "credential": {"token": "t"},
        },
        preserved=["inputs.p", "credential.token"],
    )
    assert preserved_fields == {"credential"}
    assert conflict_fields == ["inputs"]


def test_strip_paths_removes_dotted_path() -> None:
    obj = {"inputs": {"username": "u", "password": "p"}}
    out = SecretPreservationPolicy.strip_paths(obj, ["inputs.password"])
    assert out == {"inputs": {"username": "u"}}
    # Original untouched (deepcopy semantics).
    assert obj == {"inputs": {"username": "u", "password": "p"}}


def test_strip_paths_glob_matches_every_child() -> None:
    obj = {"inputs": {"a": 1, "b": 2}}
    out = SecretPreservationPolicy.strip_paths(obj, ["inputs.*"])
    assert out == {"inputs": {}}


def test_strip_paths_glob_in_list() -> None:
    obj = {"survey_spec": {"spec": [{"default": "x", "k": 1}, {"default": "y", "k": 2}]}}
    out = SecretPreservationPolicy.strip_paths(obj, ["survey_spec.spec.*.default"])
    assert out == {"survey_spec": {"spec": [{"k": 1}, {"k": 2}]}}


def test_strip_paths_handles_missing_path_gracefully() -> None:
    """Stripping a path that doesn't exist must not raise; absent paths
    are no-ops (the caller may pass paths declared on the spec but not
    present on this particular record)."""
    obj = {"name": "n"}
    out = SecretPreservationPolicy.strip_paths(obj, ["inputs.missing"])
    assert out == {"name": "n"}


def test_strip_paths_glob_at_leaf_on_list_root() -> None:
    """``*`` at the leaf of a list root clears the list."""
    out = SecretPreservationPolicy.strip_paths([1, 2, 3], ["*"])
    assert out == []


def test_strip_paths_non_leaf_glob_strips_key_from_every_child() -> None:
    """``*.key`` at a non-leaf depth strips ``key`` from every dict child."""
    obj = {
        "cred_a": {"user": "u1", "password": "p1"},
        "cred_b": {"user": "u2", "password": "p2"},
    }
    out = SecretPreservationPolicy.strip_paths(obj, ["*.password"])
    assert out == {"cred_a": {"user": "u1"}, "cred_b": {"user": "u2"}}


def test_strip_paths_recursion_into_none_value_is_noop() -> None:
    """Recursing into a ``None`` value is a no-op (the ``obj is None``
    guard prevents AttributeError)."""
    out = SecretPreservationPolicy.strip_paths({"a": None}, ["a.k"])
    assert out == {"a": None}


@pytest.mark.parametrize(
    ("obj", "path"),
    [
        ({"a": "scalar"}, "a.k"),  # leaf-level: scalar where dict/list expected
        ({"a": 42}, "a.k.v"),  # non-leaf: scalar at mid-path
    ],
)
def test_strip_paths_descending_into_scalar_is_noop(obj: dict, path: str) -> None:
    """Paths that descend into a scalar (wrong shape) silently no-op
    rather than raise — defensive contract for malformed payloads."""
    out = SecretPreservationPolicy.strip_paths(obj, [path])
    assert out == obj

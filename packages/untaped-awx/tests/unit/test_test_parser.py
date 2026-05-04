"""Frontmatter splitter, !ref YAML tag, and Jinja2 environment."""

from __future__ import annotations

import pytest
import yaml
from jinja2 import UndefinedError
from untaped_awx.domain.test_suite import RefSentinel
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.test.parser import (
    build_jinja_env,
    load_yaml_with_refs,
    split_frontmatter,
)

# ---- frontmatter splitter ------------------------------------------------


def test_split_frontmatter_basic() -> None:
    text = "---\nvariables:\n  env: {}\n---\nkind: AwxTestSuite\n"
    meta, body = split_frontmatter(text)
    assert "variables:" in meta
    assert body.strip().startswith("kind:")


def test_split_frontmatter_no_frontmatter() -> None:
    """Body-only files have an empty metadata section."""
    text = "kind: AwxTestSuite\n"
    meta, body = split_frontmatter(text)
    assert meta == ""
    assert body == text


def test_split_frontmatter_missing_close_delimiter_errors() -> None:
    text = "---\nvariables:\n  env: {}\nkind: AwxTestSuite\n"
    with pytest.raises(AwxApiError, match="frontmatter"):
        split_frontmatter(text)


def test_split_frontmatter_strips_leading_blank_lines() -> None:
    text = "\n\n---\nvariables: {}\n---\nbody: y\n"
    meta, body = split_frontmatter(text)
    assert "variables" in meta
    assert "body: y" in body


# ---- !ref tag -----------------------------------------------------------


def test_ref_sentinel_is_distinct_from_dict() -> None:
    """``!ref { kind, name }`` parses to a RefSentinel — not a dict."""
    text = 'inventory: !ref { kind: Inventory, name: "Web Inventory" }\n'
    parsed = load_yaml_with_refs(text)
    assert isinstance(parsed["inventory"], RefSentinel)
    assert parsed["inventory"].kind == "Inventory"
    assert parsed["inventory"].name == "Web Inventory"


def test_bare_dict_with_name_and_kind_keys_remains_a_dict() -> None:
    """User content that happens to have ``name``/``kind`` is left alone."""
    text = "user:\n  name: Alice\n  kind: admin\n"
    parsed = load_yaml_with_refs(text)
    assert isinstance(parsed["user"], dict)
    assert parsed["user"] == {"name": "Alice", "kind": "admin"}


def test_ref_inside_list_resolves_per_item() -> None:
    text = 'credentials:\n  - !ref { kind: Credential, name: "github-pat" }\n  - 42\n'
    parsed = load_yaml_with_refs(text)
    assert isinstance(parsed["credentials"][0], RefSentinel)
    assert parsed["credentials"][1] == 42


def test_ref_requires_kind_and_name() -> None:
    text = "inventory: !ref { kind: Inventory }\n"
    with pytest.raises(AwxApiError, match="!ref"):
        load_yaml_with_refs(text)


# ---- Jinja2 env ---------------------------------------------------------


def test_strict_undefined_raises_on_missing_var() -> None:
    env = build_jinja_env()
    template = env.from_string("hello {{ name }}")
    with pytest.raises(UndefinedError):
        template.render({})


def test_to_yaml_filter_quotes_problem_strings() -> None:
    env = build_jinja_env()
    template = env.from_string("value: {{ region | to_yaml }}")
    rendered = template.render({"region": "us-east-1: prod"})
    parsed = yaml.safe_load(rendered)
    assert parsed["value"] == "us-east-1: prod"  # round-trips through YAML


def test_to_yaml_filter_handles_lists() -> None:
    env = build_jinja_env()
    template = env.from_string("regions: {{ items | to_yaml }}")
    rendered = template.render({"items": ["us-east-1", "eu-west-1"]})
    parsed = yaml.safe_load(rendered)
    assert parsed["regions"] == ["us-east-1", "eu-west-1"]


def test_to_json_filter() -> None:
    env = build_jinja_env()
    template = env.from_string("v: {{ obj | to_json }}")
    rendered = template.render({"obj": {"a": 1, "b": [2, 3]}})
    parsed = yaml.safe_load(rendered)  # JSON is valid YAML
    assert parsed["v"] == {"a": 1, "b": [2, 3]}


def test_to_yaml_filter_preserves_user_trailing_ellipsis() -> None:
    """A literal ``release...`` value must not have its dots stripped.

    PyYAML emits ``release...\\n...\\n`` for that scalar; the end-of-doc
    marker must come off without touching the user's own trailing dots.
    """
    env = build_jinja_env()
    template = env.from_string("value: {{ tag | to_yaml }}\n")
    rendered = template.render({"tag": "release..."})
    parsed = yaml.safe_load(rendered)
    assert parsed["value"] == "release..."


def test_to_yaml_filter_does_not_emit_document_end_marker() -> None:
    """``...`` would split the rendered body into two YAML documents."""
    env = build_jinja_env()
    template = env.from_string("limit: {{ region | to_yaml }}\nnext_field: y\n")
    rendered = template.render({"region": "prod"})
    assert "..." not in rendered
    # Whole rendered body parses as ONE document with both keys.
    parsed = yaml.safe_load(rendered)
    assert parsed == {"limit": "prod", "next_field": "y"}

"""Tests for the shared hook helpers module (in-process and worker)."""

from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest

from untaped.capabilities.recipe.domain import templates
from untaped.capabilities.recipe.hook_worker import HookHelpers, render_template

HELPERS_FILE = Path(inspect.getfile(HookHelpers))


def test_one_renderer_serves_domain_and_hooks() -> None:
    assert templates.render_template is render_template
    assert Path(inspect.getfile(render_template)) == HELPERS_FILE


def test_render_template_replaces_defined_inputs() -> None:
    assert render_template("owner={{ owner }}", {"owner": "platform"}) == "owner=platform"
    assert (
        render_template("owner={{ owner }}", {"owner": "platform"}, unknown_tokens="keep")
        == "owner=platform"
    )


def test_render_template_handles_unknown_bare_names() -> None:
    with pytest.raises(ValueError, match="template input 'owner' is not defined"):
        render_template("owner={{ owner }}", {})

    assert render_template("owner={{ owner }}", {}, unknown_tokens="keep") == "owner={{ owner }}"


def test_render_template_rejects_structured_values() -> None:
    with pytest.raises(
        ValueError,
        match="structured input 'cols' cannot be rendered; hooks receive it natively",
    ):
        render_template("cols={{ cols }}", {"cols": ["a", "b"]})


@pytest.mark.parametrize("template", ["${{ github.ref }}", "{{ .Values.x }}"])
def test_render_template_rejects_non_bare_tokens_by_default(template: str) -> None:
    token = "{{ github.ref }}" if "github" in template else "{{ .Values.x }}"
    message = (
        f"template token {token!r} is not a bare input name; "
        "set unknown_tokens: keep to pass it through"
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        render_template(template, {})

    assert render_template(template, {}, unknown_tokens="keep") == template


def test_render_template_rejects_invalid_unknown_token_mode() -> None:
    with pytest.raises(ValueError, match="unknown_tokens"):
        render_template("{{ owner }}", {"owner": "platform"}, unknown_tokens="passthrough")


def test_helpers_return_wire_ready_verdicts_and_collect_warnings() -> None:
    helpers = HookHelpers()

    helpers.warn("first")
    helpers.warn("second")

    assert helpers.pass_() == {"status": "pass", "message": ""}
    assert helpers.skip("n/a") == {"status": "skip", "message": "n/a"}
    assert helpers.fail("bad") == {"status": "fail", "message": "bad"}
    assert helpers.warnings == ["first", "second"]
    assert HookHelpers().warnings == []


def test_helpers_dump_yaml_round_trips_by_default_and_honours_options() -> None:
    helpers = HookHelpers()
    long_line = "k: " + " ".join(["y"] * 60) + "\n"
    data = helpers.load_yaml("items:\n- 'quoted'\n" + long_line)

    # Defaults keep quotes and never wrap long lines (width 4096).
    assert helpers.dump_yaml(data) == "items:\n- 'quoted'\n" + long_line
    formatted = helpers.dump_yaml(
        {"items": [1], "k": "aa bb cc dd ee ff"},
        options={
            "indent": {"mapping": 2, "sequence": 4, "offset": 2},
            "width": 10,
            "explicit_start": True,
            "explicit_end": True,
        },
    )
    assert formatted.startswith("---\nitems:\n  - 1\n")
    assert "\n  cc dd ee\n" in formatted
    assert formatted.endswith("\n...\n")
    assert helpers.dump_yaml({"items": [1]}, options={"block_seq_indent": 2}) == "items:\n  - 1\n"


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"preserve_quote": True}, "unsupported YAML dump option"),
        ({"indent": {"seqence": 4}}, "unsupported YAML indent option"),
        ({"width": "100"}, "must be an integer"),
        ({"width": True}, "must be an integer"),
        ({"preserve_quotes": "yes"}, "must be a boolean"),
        ({"indent": 2}, "must be a mapping"),
    ],
)
def test_dump_yaml_rejects_invalid_options(options: dict[str, object], message: str) -> None:
    with pytest.raises(TypeError, match=message):
        HookHelpers().dump_yaml({"items": [1]}, options=options)


def test_helpers_module_is_stdlib_only_at_import() -> None:
    # The hook worker loads this file by path inside a pack environment where
    # neither untaped nor ruamel.yaml is guaranteed to be importable.
    tree = ast.parse(HELPERS_FILE.read_text(encoding="utf-8"))
    top_level = [node for node in tree.body if isinstance(node, ast.Import | ast.ImportFrom)]
    modules = {
        alias.name.split(".")[0] if isinstance(node, ast.Import) else (node.module or "")
        for node in top_level
        for alias in node.names
    }
    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    assert {module.split(".")[0] for module in modules} <= stdlib

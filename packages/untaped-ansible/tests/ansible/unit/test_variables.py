"""Tests for the static analysis of Ansible variable definitions, sets and reads."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from untaped_ansible.domain import variables
from untaped_ansible.domain.variables import (
    FileAnalysis,
    VariableFact,
    analyse,
    defines,
    selected,
    uses,
)

Row = tuple[str, int, list[str | int], str]


def _rows(path: str, text: str, name: str = "haku_port") -> list[Row]:
    """Return ``(file, line, key_path, how)`` of the facts about ``name``."""
    analysis = analyse(path, textwrap.dedent(text).lstrip("\n"))
    assert analysis.skipped is None
    return [(f.file, f.line, f.key_path, f.how) for f in analysis.facts if f.name == name]


# --- selected ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("x.yaml", "yaml"),
        ("site.yml", "yaml"),
        ("group_vars/all", "yaml"),
        ("inventories/prod/host_vars/web1", "yaml"),
        ("group_vars/all/main.yml", "yaml"),
        ("roles/r/tasks/main.yml", "yaml"),
        ("roles/r/templates/x.yml", "template"),
        ("roles/r/templates/deep/x.conf", "template"),
        ("templates/a.conf", "template"),
        ("files/app.conf.j2", "template"),
        ("roles/r/files/x.yml", "yaml"),
        (".gitlab-ci.yml", None),
        ("sub/.gitlab-ci.yml", None),
        (".github/workflows/ci.yml", None),
        (".gitlab/ci/build.yml", None),
        ("README.md", None),
        ("group_vars", None),
        ("templates", None),
    ],
)
def test_selected(path: str, expected: str | None) -> None:
    assert selected(path) == expected


def test_a_binary_file_has_no_facts() -> None:
    assert analyse("templates/logo.png", "\x89PNG\x00{{ x }}") == FileAnalysis(facts=[])


def test_unselected_file_has_no_facts() -> None:
    assert analyse(".github/workflows/ci.yml", "haku_port: 1\n") == FileAnalysis(facts=[])


# --- sets ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "text", "expected"),
    [
        ("group_vars/prod.yml", "haku_port: 1\n", [(1, ["haku_port"])]),
        ("host_vars/h", "a: 1\nhaku_port: 1\n", [(2, ["haku_port"])]),
        ("group_vars/all", "haku_port: 1\n", [(1, ["haku_port"])]),
        (
            "inventory.yml",
            """
            all:
              children:
                web:
                  vars:
                    haku_port: 8080
            """,
            [(5, ["all", "children", "web", "vars", "haku_port"])],
        ),
        (
            "site.yml",
            """
            - hosts: web
              roles:
                - role: x
                  haku_port: 1
            """,
            [(4, [0, "roles", 0, "haku_port"])],
        ),
        (
            "site.yml",
            """
            - hosts: web
              vars:
                haku_port: 1
            """,
            [(3, [0, "vars", "haku_port"])],
        ),
        (
            "roles/r/tasks/main.yml",
            """
            - ansible.builtin.include_role:
                name: x
              vars:
                haku_port: 1
            """,
            [(4, [0, "vars", "haku_port"])],
        ),
        (
            "roles/r/tasks/main.yml",
            """
            - debug: msg=hi
            - ansible.builtin.set_fact: {haku_port: 1}
            """,
            [(2, [1, "ansible.builtin.set_fact", "haku_port"])],
        ),
        (
            "roles/r/tasks/main.yml",
            """
            - command: whoami
              register: haku_port
            """,
            [(2, [0, "register"])],
        ),
        (
            "roles/r/tasks/main.yml",
            """
            - debug: var=x
              loop: [1]
              loop_control: {loop_var: haku_port}
            """,
            [(3, [0, "loop_control", "loop_var"])],
        ),
    ],
)
def test_sets(path: str, text: str, expected: list[tuple[int, list[str | int]]]) -> None:
    assert _rows(path, text) == [(path, line, key_path, "sets") for line, key_path in expected]


def test_every_key_is_a_set_but_dotted_keys_are_only_path_segments() -> None:
    rows = analyse("site.yml", "- hosts: web\n  ansible.builtin.ping: {}\n").facts
    assert [(f.name, f.key_path) for f in rows] == [("hosts", [0, "hosts"])]


def test_register_with_template_value_is_not_a_set() -> None:
    assert _rows("t.yml", "- register: '{{ haku_port }}'\n") == [
        ("t.yml", 1, [0, "register"], "reads")
    ]


def test_loop_var_outside_loop_control_is_not_a_set() -> None:
    assert _rows("t.yml", "loop_var: haku_port\n") == []


# --- reads in YAML -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("- debug: var=x\n  when: haku_port > 1\n", [(2, [0, "when"])]),
        ("- debug: var=x\n  when: '{{ haku_port }}'\n", [(2, [0, "when"])]),
        ("- debug: var=x\n  until: haku_port is version('1', '>')\n", [(2, [0, "until"])]),
        (
            "- assert:\n    that:\n      - x is defined\n      - haku_port | int > 1\n",
            [(4, [0, "assert", "that", 1])],
        ),
        ("- debug:\n    msg: '{{ haku_port }}'\n", [(2, [0, "debug", "msg"])]),
        ("- debug:\n    msg: ['a', '{{ haku_port }}']\n", [(2, [0, "debug", "msg", 1])]),
        ("x: \"{{ hostvars[inventory_hostname]['haku_port'] }}\"\n", [(1, ["x"])]),
        ("x: \"{{ vars['haku_port'] }}\"\n", [(1, ["x"])]),
        ("x: \"{{ lookup('vars', 'other', 'haku_port') }}\"\n", [(1, ["x"])]),
        ("x: '{{ haku_port.split(\",\") }}'\n", [(1, ["x"])]),
    ],
)
def test_yaml_reads(text: str, expected: list[tuple[int, list[str | int]]]) -> None:
    assert _rows("t.yml", text) == [("t.yml", line, kp, "reads") for line, kp in expected]


def test_bare_expression_keys_only_read_their_own_values() -> None:
    assert _rows("t.yml", "- debug:\n    msg: haku_port > 1\n") == []


def test_read_in_literal_block_reports_its_own_line() -> None:
    text = """
    - copy:
        content: |
          first
          second {{ other }}
          third {{ haku_port }}
    """
    assert _rows("t.yml", text) == [("t.yml", 5, [0, "copy", "content"], "reads")]
    assert _rows("t.yml", text, "other") == [("t.yml", 4, [0, "copy", "content"], "reads")]


def test_read_in_folded_block_reports_its_first_content_line() -> None:
    text = """
    - debug:
        msg: >
          first
          {{ haku_port }}
    """
    assert _rows("t.yml", text) == [("t.yml", 3, [0, "debug", "msg"], "reads")]


def test_multi_document_paths_start_with_the_document_index() -> None:
    text = """
    a: 1
    ---
    ---
    haku_port: '{{ haku_port_default }}'
    """
    assert _rows("t.yml", text) == [("t.yml", 4, [2, "haku_port"], "sets")]
    assert _rows("t.yml", text, "haku_port_default") == [("t.yml", 4, [2, "haku_port"], "reads")]


def test_single_document_with_markers_has_no_document_index() -> None:
    assert _rows("t.yml", "---\nhaku_port: 1\n...\n") == [("t.yml", 2, ["haku_port"], "sets")]


@pytest.mark.parametrize(
    "text",
    [
        "- debug:\n    msg: '{{ item.haku_port }}'\n",
        "x: '{% for haku_port in xs %}{{ haku_port }}{% endfor %}'\n",
        "# haku_port: 1\nx: 1\n",
        "x: \"{{ 'haku_port' }}\"\n",
        "x: haku_port\n",
        "x: !unsafe '{{ haku_port }}'\n",
        "x: '{% set haku_port = 1 %}{{ haku_port }}'\n",
        "x: '{% macro m(haku_port) %}{{ haku_port }}{% endmacro %}'\n",
    ],
)
def test_not_a_fact(text: str) -> None:
    assert _rows("t.yml", text) == []


def test_merge_key_is_neither_recorded_nor_walked() -> None:
    text = """
    base: &base {other: 1}
    web:
      <<: *base
      x: 1
    """
    facts = analyse("t.yml", textwrap.dedent(text).lstrip("\n")).facts
    assert [(f.name, f.key_path) for f in facts] == [
        ("base", ["base"]),
        ("other", ["base", "other"]),
        ("web", ["web"]),
        ("x", ["web", "x"]),
    ]


def test_recursive_alias_is_walked_once() -> None:
    facts = analyse("t.yml", "a: &x ['{{ haku_port }}', *x]\n").facts
    assert [(f.name, f.key_path, f.how) for f in facts] == [
        ("a", ["a"], "sets"),
        ("haku_port", ["a", 0], "reads"),
    ]


# --- defines -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "defaults/main.yml",
        "vars/x/y.yml",
        "roles/web/defaults/main.yml",
        "roles/r/vars/main.yaml",
    ],
)
def test_defines_files_define_top_level_keys_only(path: str) -> None:
    text = """
    haku_port: 1
    haku_opts:
      haku_nested: '{{ haku_base }}'
    """
    analysis = analyse(path, textwrap.dedent(text).lstrip("\n"))
    assert [(f.name, f.line, f.key_path, f.how) for f in analysis.facts] == [
        ("haku_port", 1, ["haku_port"], "defines"),
        ("haku_opts", 2, ["haku_opts"], "defines"),
        ("haku_base", 3, ["haku_opts", "haku_nested"], "reads"),
    ]


def test_role_tasks_under_a_vars_named_dir_are_not_defines() -> None:
    assert _rows("roles/r/tasks/vars/x.yml", "haku_port: 1\n") == [
        ("roles/r/tasks/vars/x.yml", 1, ["haku_port"], "sets")
    ]


@pytest.mark.parametrize("path", ["meta/argument_specs.yml", "roles/r/meta/argument_specs.yaml"])
def test_argument_spec_options_define(path: str) -> None:
    text = """
    argument_specs:
      main:
        short_description: Web
        options:
          haku_port:
            type: int
            default: 80
    """
    analysis = analyse(path, textwrap.dedent(text).lstrip("\n"))
    assert [(f.name, f.line, f.key_path, f.how) for f in analysis.facts] == [
        ("haku_port", 5, ["argument_specs", "main", "options", "haku_port"], "defines"),
    ]


# --- templates -----------------------------------------------------------------------


def test_template_file_reads_have_their_lines_and_no_key_path() -> None:
    text = "line one\n{% if x %}\n  port={{ haku_port }}\n{% endif %}\n"
    assert _rows("roles/r/templates/app.conf.j2", text) == [
        ("roles/r/templates/app.conf.j2", 3, [], "reads")
    ]


def test_yaml_under_templates_is_read_as_a_template() -> None:
    analysis = analyse("templates/app.yml", "haku_port: {{ other }}\n")
    assert [(f.name, f.line, f.key_path, f.how) for f in analysis.facts] == [
        ("other", 1, [], "reads")
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{% do x.append(haku_port) %}", {"x", "haku_port"}),
        ("{% for i in r %}{% break %}{% continue %}{% endfor %}", {"r"}),
        ("{{ p | dirname }}{{ d | to_json(indent=n) }}", {"p", "d", "n"}),
        ("{{ v is version('1.0', '>=') }}", {"v"}),
        ("{{ item.haku_port }}", {"item"}),
        ("{% for k, v in m.items() if v %}{{ k }}{{ loop.index }}{% endfor %}", {"m"}),
        ("{% for i in xs %}{% else %}{{ i }}{% endfor %}", {"xs"}),
        ("{{ a }}{% set a = b %}{{ a }}", {"a", "b"}),
        ("{% set a %}{{ c }}{% endset %}{{ a }}", {"c"}),
        ("{% set ns = namespace(x=1) %}{% set ns.x = y %}", {"namespace", "y"}),
        (
            "{% macro m(a, b=d) %}{{ a }}{{ b }}{{ e }}{{ caller() }}{% endmacro %}{{ m(1) }}",
            {
                "d",
                "e",
            },
        ),
        ("{% call(u) m(f) %}{{ u }}{{ g }}{% endcall %}", {"m", "f", "g"}),
        ("{% with w = h %}{{ w }}{% endwith %}{{ w }}", {"h", "w"}),
        ("{% import 'x.j2' as lib %}{% from 'y' import z as zz %}{{ lib.f(zz) }}", set()),
        ("{{ hostvars[h].haku_port }}", {"hostvars", "h", "haku_port"}),
        ("{{ vars.haku_port }}", {"vars", "haku_port"}),
        ("{{ x['haku_port'] }}", {"x"}),
    ],
)
def test_template_read_names(text: str, expected: set[str]) -> None:
    assert {f.name for f in analyse("t.j2", text).facts} == expected


# --- skipped -------------------------------------------------------------------------


def test_broken_yaml_has_no_facts_and_says_why() -> None:
    analysis = analyse("t.yml", "a: 1\nb: [\n")
    assert analysis.facts == []
    assert analysis.skipped is not None
    assert "line" in analysis.skipped


def test_broken_template_has_no_facts_and_says_where() -> None:
    analysis = analyse("t.j2", "ok {{ a }}\n{% if %}\n")
    assert analysis.facts == []
    assert analysis.skipped is not None
    assert analysis.skipped.startswith("2: ")


def test_one_bad_jinja_scalar_keeps_the_other_facts() -> None:
    text = "- debug:\n    msg: '{{ broken'\n- debug:\n    msg: '{{ haku_port }}'\n"
    analysis = analyse("t.yml", text)
    assert analysis.skipped is not None
    assert analysis.skipped.startswith("partly: 2: ")
    assert ("haku_port", 4, "reads") in [(f.name, f.line, f.how) for f in analysis.facts]


def test_nesting_too_deep_is_skipped_not_raised() -> None:
    analysis = analyse("t.yml", "a: " + "[" * 5000 + "]" * 5000 + "\n")
    assert analysis.facts == []
    assert analysis.skipped is not None


@pytest.mark.parametrize("text", ["", "\x00", "﻿", "- {{\n", "!!python/object:os.system x", "a: *b"])
def test_never_raises(text: str) -> None:
    assert isinstance(analyse("t.yml", text), FileAnalysis)
    assert isinstance(analyse("t.j2", text), FileAnalysis)


# --- results -------------------------------------------------------------------------


def test_facts_are_sorted_deduplicated_and_deterministic() -> None:
    text = "x: '{{ b }}{{ a }}{{ a }}'\ny: '{{ a }}'\n"
    first = analyse("t.yml", text)
    assert first == analyse("t.yml", text)
    assert [(f.name, f.line, f.key_path, f.how) for f in first.facts] == [
        ("a", 1, ["x"], "reads"),
        ("b", 1, ["x"], "reads"),
        ("x", 1, ["x"], "sets"),
        ("a", 2, ["y"], "reads"),
        ("y", 2, ["y"], "sets"),
    ]


def test_models_are_frozen() -> None:
    fact = VariableFact(name="a", file="t.yml", line=1, key_path=[], how="reads")
    with pytest.raises(ValueError, match="frozen"):
        fact.name = "b"  # type: ignore[misc]


def test_defines_and_uses() -> None:
    files = {
        "roles/web/defaults/main.yml": "haku_port: 80\n",
        "roles/web/tasks/main.yml": "- debug:\n    msg: '{{ haku_port }} {{ other }}'\n",
        "README.md": "haku_port",
    }
    assert defines(files) == {"haku_port"}
    assert uses(files, {"haku_port"}) == [
        VariableFact(
            name="haku_port",
            file="roles/web/defaults/main.yml",
            line=1,
            key_path=["haku_port"],
            how="defines",
        ),
        VariableFact(
            name="haku_port",
            file="roles/web/tasks/main.yml",
            line=2,
            key_path=[0, "debug", "msg"],
            how="reads",
        ),
    ]


def test_module_imports_nothing_but_its_allowed_dependencies() -> None:
    allowed = {"__future__", "re", "collections", "typing", "jinja2", "yaml", "pydantic"}
    tree = ast.parse(Path(variables.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module.split(".")[0])
    assert imported <= allowed

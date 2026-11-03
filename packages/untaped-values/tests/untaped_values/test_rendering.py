"""``render`` (strict, per field), ``redact``, and the renderer file's standard-library promise."""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

from untaped_values.api import Input, InputError, placeholders, redact, render, renderer_file

_SPECS = {
    "name": Input(),
    "token": Input(secret=True),
    "cols": Input(type="list"),
}


def test_render_puts_values_into_text() -> None:
    assert render("{{ name }}/{{name}}.yml", {"name": "api"}, _SPECS, field="dest") == "api/api.yml"


@pytest.mark.parametrize(
    ("text", "values", "message"),
    [
        ("{{ token }}", {"token": "s3cret"}, "secret input 'token' cannot be used in field 'dest'"),
        ("{{ cols }}", {"cols": ["a"]}, "structured input 'cols' cannot be used in field 'dest'"),
        ("{{ other }}", {"other": {"a": 1}}, "structured input 'other' cannot be used in field"),
        ("{{ missing }}", {}, "input 'missing' in field 'dest' has no value"),
        ("{{ a.b }}", {}, "token '{{ a.b }}' in field 'dest' is not a bare input name"),
    ],
)
def test_render_refuses_what_a_field_cannot_hold(
    text: str, values: dict[str, object], message: str
) -> None:
    with pytest.raises(InputError, match=message) as excinfo:
        render(text, values, _SPECS, field="dest")
    assert "s3cret" not in str(excinfo.value)


def test_redact_hides_secrets_and_drops_undeclared_names() -> None:
    assert redact(_SPECS, {"name": "api", "token": "s3cret", "stray": 1}) == {
        "name": "api",
        "token": "***",
    }


def test_the_renderer_file_renders_with_only_the_standard_library() -> None:
    """``-I -S``: no site-packages, so neither untaped nor any third-party package is importable."""
    script = textwrap.dedent(
        """
        import importlib.util, sys
        spec = importlib.util.spec_from_file_location("renderer", sys.argv[1])
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        text = "hi {{ name }} {{ x.y }}"
        print(module.render_template(text, {"name": "ada"}, unknown_tokens="keep"))
        try:
            module.render_template("{{ nope }}", {})
        except ValueError as exc:
            print(exc)
        print(sorted(m for m in sys.modules if m.startswith("untaped")))
        """
    )
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", script, str(renderer_file())],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.splitlines() == [
        "hi ada {{ x.y }}",
        "template input 'nope' is not defined",
        "[]",
    ]


def test_placeholders_name_each_input_a_field_uses_once_in_order() -> None:
    assert placeholders("{{ b }}/{{a}}/{{ b }}", field="target") == ("b", "a")
    assert placeholders("no tokens", field="target") == ()


def test_placeholders_refuse_a_token_that_is_not_a_bare_name() -> None:
    with pytest.raises(InputError, match=r"token '\{\{ a\.b \}\}' in field 'target' is not a bare"):
        placeholders("~/{{ a.b }}", field="target")

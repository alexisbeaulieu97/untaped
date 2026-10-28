"""A credential's override: ``UNTAPED_<PLUGIN>__<NAME>`` or the ``UNTAPED_<PLUGIN>`` JSON."""

from __future__ import annotations

import pytest

from untaped.sdk.network.credentials import override_variable


@pytest.mark.parametrize(
    ("env", "named"),
    [
        ({"UNTAPED_DEMO__TOKEN": "t"}, "UNTAPED_DEMO__TOKEN"),
        ({"untaped_demo__token": "t"}, "UNTAPED_DEMO__TOKEN"),
        ({"UNTAPED_DEMO": '{"token": "t"}'}, "UNTAPED_DEMO"),
        ({"UNTAPED_DEMO__TOKEN": "  "}, None),
        ({"UNTAPED_DEMO": '{"token": " "}'}, None),
        ({"UNTAPED_DEMO": '{"other": "t"}'}, None),
        ({"UNTAPED_DEMO": '["token"]'}, None),
        ({"UNTAPED_DEMO": "{not json"}, None),
    ],
    ids=["leaf", "any-case", "json", "blank", "blank-json", "other-name", "not-object", "bad"],
)
def test_the_override_names_its_variable(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], named: str | None
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert override_variable("demo", "token") == named

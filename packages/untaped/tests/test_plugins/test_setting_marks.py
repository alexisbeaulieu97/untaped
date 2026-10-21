"""Setting marks: what composition refuses, what it adds, and what stays unchanged.

``NeedsSetup`` and ``Secret`` only take effect on the fields they fit, so
every misplaced mark quarantines its plugin at load. Each ``Secret`` gets a
``<field>_command`` setting from core; a model declaring it itself loads
and lint warns. The marks change neither the JSON schema nor the ``cfg``
surface lines.
"""

from __future__ import annotations

from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, SecretStr

from test_plugins.plugin_harness import make_shell, make_spec
from untaped import bootstrap
from untaped.auth import resolve_token
from untaped.plugins.registry import CompositionResult, compose, section_settings
from untaped.sdk import NeedsSetup, Secret, Suggest, TokenCommand, TokenSources
from untaped.stability import forget_notices
from untaped.surface._settings import settings_lines
from untaped.testing import check_plugin, plugin_candidate


class Plain(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class Bare(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None


class Marked(BaseModel):
    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("MARKED_TOKEN",))] = None


class Hooked(BaseModel):
    hook: Annotated[SecretStr | None, NeedsSetup, Secret(env=("HOOK_SECRET",))] = None


class SelfDeclared(BaseModel):
    token_sources: ClassVar[TokenSources] = TokenSources(env=("OWN_TOKEN",))

    token: Annotated[SecretStr | None, NeedsSetup, Secret(env=("OWN_TOKEN",))] = None
    token_command: TokenCommand = None


class MarkedBool(BaseModel):
    enabled: Annotated[bool, NeedsSetup] = False


class MarkedWithDefault(BaseModel):
    base_url: Annotated[str | None, NeedsSetup] = "https://example.com"


class SecretOnStr(BaseModel):
    token: Annotated[str | None, Secret()] = None


class Inner(BaseModel):
    token: Annotated[SecretStr | None, Secret()] = None


class NestedSecret(BaseModel):
    inner: Inner = Inner()


class StrCommand(BaseModel):
    token: Annotated[SecretStr | None, Secret()] = None
    token_command: str | None = None


class EnvMismatch(BaseModel):
    token_sources: ClassVar[TokenSources] = TokenSources(env=("A_TOKEN",))

    token: Annotated[SecretStr | None, Secret(env=("B_TOKEN",))] = None


class MarkInsideType(BaseModel):
    base_url: Annotated[str, NeedsSetup] | None = None


class MarkedModel(BaseModel):
    inner: Annotated[Inner | None, NeedsSetup] = None


def _compose(model: type[BaseModel]) -> CompositionResult:
    return compose(make_shell(), [plugin_candidate(make_spec("alpha", settings=model))])


@pytest.mark.parametrize(
    ("model", "says"),
    [
        (MarkedBool, "'enabled' is marked NeedsSetup but is not optional with a None default"),
        (MarkedWithDefault, "'base_url' is marked NeedsSetup but is not optional"),
        (SecretOnStr, "'token' is marked Secret but is not a SecretStr | None"),
        (NestedSecret, "'inner.token' is marked Secret inside a nested model"),
        (StrCommand, "'token_command' is the command setting untaped adds for a Secret"),
        (EnvMismatch, "token_sources names A_TOKEN but the Secret on 'token' names B_TOKEN"),
        (MarkInsideType, "sits inside its type"),
        (MarkedModel, "'inner' is a model; annotate its fields instead"),
    ],
)
def test_a_misplaced_mark_quarantines_its_plugin(model: type[BaseModel], says: str) -> None:
    (record,) = _compose(model).quarantine
    assert record.reason == "bad-settings-keys"
    assert record.detail.startswith("plugin 'alpha': ")
    assert says in record.detail


@pytest.mark.parametrize("model", [Marked, Hooked, SelfDeclared, Plain])
def test_marks_that_fit_load(model: type[BaseModel]) -> None:
    assert _compose(model).quarantine == ()


def test_core_adds_a_command_setting_for_each_secret() -> None:
    marked = section_settings(make_spec("alpha", settings=Marked))
    hooked = section_settings(make_spec("alpha", settings=Hooked))
    assert marked is not None and hooked is not None
    assert marked.model_fields["token_command"].description == (
        "Command (argv list, no shell) that prints the token; used when `alpha.token` is unset."
    )
    assert "hook_command" in hooked.model_fields
    assert "token_command" not in hooked.model_fields


def test_a_self_declared_command_is_kept_as_declared() -> None:
    assert section_settings(make_spec("alpha", settings=SelfDeclared)) in (None, SelfDeclared)


@pytest.mark.usefixtures("fresh_composition")
@pytest.mark.parametrize(
    ("model", "check", "subject"),
    [
        (SelfDeclared, "self-declared-command", "declaring alpha.token_command"),
        (Plain, "unmarked-service", "setting up alpha from its base_url and token names"),
    ],
)
def test_lint_warns_about_each_deprecated_way(
    model: type[BaseModel], check: str, subject: str
) -> None:
    spec = make_spec("alpha", settings=model)
    rows = check_plugin("alpha", candidates=[plugin_candidate(spec)])
    (row,) = [row for row in rows if row.check == check]
    assert (row.title, row.status) == (model.__name__, "warn")
    assert subject in row.detail
    # It loads: no registration row (only this test module's own imports fail conventions).
    assert all(row.check != "registration" for row in rows)


@pytest.mark.usefixtures("fresh_composition")
def test_marks_change_neither_the_schema_nor_the_cfg_lines() -> None:
    def schema(model: type[BaseModel]) -> dict[str, object]:
        return {k: v for k, v in model.model_json_schema().items() if k != "title"}

    assert schema(Bare) == schema(Marked)
    result = bootstrap.compose_root(
        candidates=[
            plugin_candidate(make_spec("plain", settings=Plain)),
            plugin_candidate(make_spec("marked", settings=Marked)),
        ]
    )
    texts = [line.text for line in settings_lines(result, root=False)]
    plain = sorted(t.removeprefix("cfg plain.") for t in texts if t.startswith("cfg plain."))
    marked = sorted(t.removeprefix("cfg marked.") for t in texts if t.startswith("cfg marked."))
    assert plain == marked
    assert "token_command string[]? default=unset" in marked


def test_the_token_resolves_from_the_injected_command_then_the_marked_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composed = section_settings(make_spec("alpha", settings=Marked))
    assert composed is not None
    monkeypatch.setenv("MARKED_TOKEN", "from-env")
    by_command = resolve_token(composed(token_command=["printf", "run"]), section="alpha")
    assert by_command.token is not None
    assert by_command.token.get_secret_value() == "run"
    by_env = resolve_token(composed(), section="alpha")
    assert by_env.token is not None
    assert by_env.token.get_secret_value() == "from-env"


def test_the_only_secret_resolves_whatever_its_name(monkeypatch: pytest.MonkeyPatch) -> None:
    composed = section_settings(make_spec("alpha", settings=Hooked))
    assert composed is not None
    monkeypatch.setenv("HOOK_SECRET", "hooked")
    resolved = resolve_token(composed(), section="alpha")
    assert resolved.hook is not None
    assert resolved.hook.get_secret_value() == "hooked"


@pytest.mark.parametrize(("model", "field"), [(Marked, "token"), (Hooked, "hook")])
def test_each_plaintext_secret_warns_naming_auth_migrate(
    capsys: pytest.CaptureFixture[str], model: type[BaseModel], field: str
) -> None:
    forget_notices()
    composed = section_settings(make_spec("alpha", settings=model))
    assert composed is not None
    resolve_token(composed(**{field: SecretStr("plain")}), section="alpha")
    err = capsys.readouterr().err
    assert f"plain-text alpha.{field}" in err
    assert "untaped auth migrate" in err


@pytest.mark.parametrize(
    "build",
    [
        lambda: Suggest(()),
        lambda: Suggest(["gh", "auth", "token"]),  # type: ignore[arg-type]
        lambda: Suggest((" ",)),
        lambda: Secret(env=["GH_TOKEN"]),  # type: ignore[arg-type]
        lambda: Secret(env=("",)),
        lambda: Secret(suggest=("gh",)),  # type: ignore[arg-type]
    ],
)
def test_a_malformed_mark_fails_where_it_is_written(build: object) -> None:
    with pytest.raises(TypeError):
        build()  # type: ignore[operator]

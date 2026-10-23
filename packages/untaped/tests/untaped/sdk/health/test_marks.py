"""Setting marks and declared credentials: what composition refuses, adds, and leaves alone.

``NeedsSetup`` only takes effect on an optional field defaulting to ``None``,
so a misplaced mark quarantines its plugin at load. A declared
:class:`~untaped.sdk.Credential` is checked the same way: its key, its name,
and the settings that hold the key. A credential's 11.0 settings
(``<name>`` and ``<name>_command``) are added to its section, deprecated,
so an 11.0 config file still reads; the marks change neither the JSON
schema nor the ``cfg`` surface lines of what a model declares.
"""

from __future__ import annotations

from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, HttpUrl, SecretStr

from core_test_support.plugins.plugin_harness import make_shell, nullary_app
from untaped.core.surface._settings import settings_lines
from untaped.runtime import bootstrap
from untaped.runtime.registry import CompositionResult, PluginSpec, compose, section_settings
from untaped.sdk import (  # type: ignore[deprecated]
    Credential,
    CredentialKey,
    NeedsSetup,
    Suggest,
    TokenCommand,
    TokenUrl,
    deprecated,
)
from untaped.sdk.testing import check_plugin, plugin_candidate


class Server(CredentialKey):
    url: HttpUrl


class Plain(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None
    token_command: TokenCommand = None


class Marked(BaseModel):
    server: Annotated[Server | None, NeedsSetup] = None


class MarkedBool(BaseModel):
    enabled: Annotated[bool, NeedsSetup] = False


class MarkedWithDefault(BaseModel):
    base_url: Annotated[str | None, NeedsSetup] = "https://example.com"


class MarkInsideType(BaseModel):
    base_url: Annotated[str, NeedsSetup] | None = None


class TwoServers(BaseModel):
    server: Server | None = None
    mirror: Server | None = None


class Clash(BaseModel):
    server: Server | None = None
    token: str | None = None


class Kept(BaseModel):
    """An 11.0 secret setting kept, deprecated, beside the credential that replaces it."""

    server: Server | None = None
    token: Annotated[SecretStr | None, deprecated(removed_in=12, replacement="auth login")] = None


class SecretKey(CredentialKey):
    url: HttpUrl
    password: SecretStr


class TwoUrls(CredentialKey):
    url: HttpUrl
    mirror: HttpUrl


class OneTokenUrl(CredentialKey):
    url: TokenUrl
    mirror: HttpUrl


class Fixed(CredentialKey):
    origin: ClassVar[HttpUrl | None] = HttpUrl("http://insecure.example.com")


def _spec(
    settings: type[BaseModel] | None, credentials: tuple[Credential[object], ...] = ()
) -> PluginSpec:
    return PluginSpec(
        name="alpha",
        app_factory=nullary_app("alpha-app"),
        settings=settings,
        credentials=credentials,  # type: ignore[arg-type]
    )


def _compose(spec: PluginSpec) -> CompositionResult:
    return compose(make_shell(), [plugin_candidate(spec)])


TOKEN = Credential("alpha", "token", key=Server, env=("ALPHA_TOKEN",))


@pytest.mark.parametrize(
    ("model", "says"),
    [
        (MarkedBool, "'enabled' is marked NeedsSetup but is not optional with a None default"),
        (MarkedWithDefault, "'base_url' is marked NeedsSetup but is not optional"),
        (MarkInsideType, "sits inside its type"),
    ],
)
def test_a_misplaced_mark_quarantines_its_plugin(model: type[BaseModel], says: str) -> None:
    (record,) = _compose(_spec(model)).quarantine
    assert record.reason == "bad-settings-keys"
    assert record.detail.startswith("plugin 'alpha': ")
    assert says in record.detail


def test_needs_setup_fits_an_optional_model() -> None:
    assert _compose(_spec(Marked, (TOKEN,))).quarantine == ()


@pytest.mark.parametrize(
    ("spec", "reason", "says"),
    [
        (
            _spec(Marked, (Credential("beta", "token", key=Server),)),
            "foreign-credential",
            "declares the credential 'beta.token' of another plugin",
        ),
        (_spec(Marked, (TOKEN, TOKEN)), "duplicate-credential", "'token' twice"),
        (
            _spec(Marked, (Credential("alpha", "token", key=SecretKey),)),
            "bad-credential",
            "password",
        ),
        (
            _spec(Marked, (Credential("alpha", "token", key=TwoUrls),)),
            "bad-credential",
            "TokenUrl",
        ),
        (_spec(Marked, (Credential("alpha", "token", key=Fixed),)), "bad-credential", "https"),
        (_spec(Clash, (TOKEN,)), "bad-credential", "has the name of a setting of its section"),
        (_spec(TwoServers, (TOKEN,)), "bad-credential", "several settings hold a Server"),
    ],
)
def test_a_credential_that_cannot_work_quarantines_its_plugin(
    spec: PluginSpec, reason: str, says: str
) -> None:
    (record,) = _compose(spec).quarantine
    assert record.reason == reason
    assert says in record.detail


def test_a_kept_secret_setting_and_one_token_url_load() -> None:
    assert _compose(_spec(Kept, (TOKEN,))).quarantine == ()
    tokened = Credential("alpha", "token", key=OneTokenUrl)
    assert _compose(_spec(None, (tokened,))).quarantine == ()


def test_core_adds_the_old_settings_deprecated() -> None:
    composed = section_settings(_spec(Marked, (TOKEN,)))
    assert composed is not None
    assert {"token", "token_command"} <= composed.model_fields.keys()
    kept = section_settings(_spec(Kept, (TOKEN,)))
    assert kept is not None
    assert kept.model_fields["token"].metadata == Kept.model_fields["token"].metadata


@pytest.mark.usefixtures("fresh_composition")
def test_lint_warns_about_an_unmarked_service() -> None:
    spec = _spec(Plain)
    rows = check_plugin("alpha", candidates=[plugin_candidate(spec)])
    (row,) = [row for row in rows if row.check == "unmarked-service"]
    assert (row.title, row.status) == ("Plain", "warn")
    assert "setting up alpha from its base_url and token names" in row.detail
    assert all(row.check != "registration" for row in rows)


@pytest.mark.usefixtures("fresh_composition")
def test_marks_change_neither_the_schema_nor_the_cfg_lines() -> None:
    class Bare(BaseModel):
        server: Server | None = None

    def schema(model: type[BaseModel]) -> dict[str, object]:
        return {k: v for k, v in model.model_json_schema().items() if k != "title"}

    assert schema(Bare) == schema(Marked)
    result = bootstrap.compose_root(
        candidates=[
            plugin_candidate(_spec(Bare)),
        ]
    )
    texts = [line.text for line in settings_lines(result, root=False)]
    assert any(text.startswith("cfg alpha.server.url") for text in texts)


@pytest.mark.parametrize(
    "build",
    [
        lambda: Suggest(()),
        lambda: Suggest(["gh", "auth", "token"]),  # type: ignore[arg-type]
        lambda: Suggest((" ",)),
        lambda: Credential("alpha", "token", key=Server, env=("",)),
        lambda: Credential("alpha", "token", key=Server, suggest=("gh",)),  # type: ignore[arg-type]
        lambda: Credential("Alpha", "token", key=Server),
        lambda: Credential("alpha", "a token", key=Server),
    ],
)
def test_a_malformed_declaration_fails_where_it_is_written(build: object) -> None:
    with pytest.raises(TypeError):
        build()  # type: ignore[operator]

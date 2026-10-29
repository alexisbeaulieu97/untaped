"""Setting marks and declared credentials: what composition refuses, adds, and leaves alone.

``NeedsSetup`` only takes effect on an optional field defaulting to ``None``,
so a misplaced mark quarantines its plugin at load. A declared
:class:`~untaped.sdk.Credential` is checked the same way: its key, its name,
and the settings that hold the key; core adds no setting for it. The marks
change neither the JSON schema nor the ``cfg`` surface lines of what a
model declares.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, ClassVar

import pytest
from pydantic import BaseModel, HttpUrl, SecretStr

from core_test_support.commands.support import make_plugin
from untaped.core.surface._settings import settings_lines
from untaped.runtime import bootstrap
from untaped.runtime.registry import CompositionResult, PluginCandidate, compose
from untaped.runtime.settings import registered_profile_model
from untaped.sdk import (
    Credential,
    CredentialKey,
    NeedsSetup,
    Suggest,
    TokenUrl,
    deprecated,
)
from untaped.sdk.testing import plugin_candidate


class Server(CredentialKey):
    url: HttpUrl


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
    """A deprecated secret setting named like the credential that replaces it."""

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


class NoSettings(BaseModel):
    """Nothing to set."""


def _alpha(settings: type[BaseModel] | None, **credentials: Credential[object]) -> PluginCandidate:
    """Plugin ``alpha`` with ``settings`` (none: an empty model) holding ``credentials``."""
    return plugin_candidate(make_plugin("alpha", settings=settings or NoSettings, **credentials))


def _compose(candidate: PluginCandidate) -> CompositionResult:
    return compose([candidate])


def _token(key: type[CredentialKey] = Server) -> Credential[object]:
    """A fresh credential: a block belongs to the one plugin class holding it."""
    return Credential(key=key, env=("ALPHA_TOKEN",))


@pytest.mark.parametrize(
    ("model", "says"),
    [
        (MarkedBool, "'enabled' is marked NeedsSetup but is not optional with a None default"),
        (MarkedWithDefault, "'base_url' is marked NeedsSetup but is not optional"),
        (MarkInsideType, "sits inside its type"),
    ],
)
def test_a_misplaced_mark_quarantines_its_plugin(model: type[BaseModel], says: str) -> None:
    (record,) = _compose(_alpha(model)).quarantine
    assert record.reason == "bad-settings-keys"
    assert record.detail.startswith("plugin 'alpha': ")
    assert says in record.detail


def test_needs_setup_fits_an_optional_model() -> None:
    assert _compose(_alpha(Marked, token=_token())).quarantine == ()


@pytest.mark.parametrize(
    ("build", "reason", "says"),
    [
        (lambda: _alpha(Marked, token=_token(SecretKey)), "bad-credential", "password"),
        (lambda: _alpha(Marked, token=_token(TwoUrls)), "bad-credential", "TokenUrl"),
        (lambda: _alpha(Marked, token=_token(Fixed)), "bad-credential", "https"),
        (
            lambda: _alpha(Clash, token=_token()),
            "bad-credential",
            "has the name of a setting of its section",
        ),
        (
            lambda: _alpha(Kept, token=_token()),
            "bad-credential",
            "has the name of a setting of its section",
        ),
        (
            lambda: _alpha(TwoServers, token=_token()),
            "bad-credential",
            "several settings hold a Server",
        ),
    ],
)
def test_a_credential_that_cannot_work_quarantines_its_plugin(
    build: Callable[[], PluginCandidate], reason: str, says: str
) -> None:
    (record,) = _compose(build()).quarantine
    assert record.reason == reason
    assert says in record.detail


def test_one_token_url_loads() -> None:
    assert _compose(_alpha(None, token=_token(OneTokenUrl))).quarantine == ()


@pytest.mark.usefixtures("fresh_composition")
def test_core_adds_no_old_settings_for_a_credential() -> None:
    bootstrap.compose_root(candidates=[_alpha(Marked, token=_token())])
    composed = registered_profile_model("alpha")
    assert composed is not None
    assert not {"token", "token_command"} & composed.model_fields.keys()


@pytest.mark.usefixtures("fresh_composition")
def test_marks_change_neither_the_schema_nor_the_cfg_lines() -> None:
    class Bare(BaseModel):
        server: Server | None = None

    def schema(model: type[BaseModel]) -> dict[str, object]:
        return {k: v for k, v in model.model_json_schema().items() if k != "title"}

    assert schema(Bare) == schema(Marked)
    result = bootstrap.compose_root(candidates=[_alpha(Bare)])
    texts = [line.text for line in settings_lines(result, owner="alpha")]
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

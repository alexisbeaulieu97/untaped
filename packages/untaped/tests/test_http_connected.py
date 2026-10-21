"""``connected_client`` takes the values it needs: ``base_url=``, ``bearer=``, ``require_token=``.

A blank value is "<section> is not set up", naming the unset marked fields
(or the parameter, for an unmarked model); leaving ``base_url=`` out keeps
the old read by field name and warns; passing both kinds refuses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from untaped.errors import ConfigError, HttpError
from untaped.http import connected_client, rejected_token_error
from untaped.sdk import NeedsSetup, Secret
from untaped.settings import HttpSettings, register_profile_settings

pytestmark = pytest.mark.usefixtures("_isolated_config")

_URL = "https://api.example.com"
_HTTP = HttpSettings()


class Marked(BaseModel):
    """The usual service: a marked URL and a marked token."""

    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret()] = None


class Hooked(BaseModel):
    """A token and a second secret."""

    base_url: Annotated[str | None, NeedsSetup] = None
    token: Annotated[SecretStr | None, NeedsSetup, Secret()] = None
    webhook_secret: Annotated[SecretStr | None, Secret()] = None


class Keyed(BaseModel):
    """One secret, not named ``token``."""

    base_url: Annotated[str | None, NeedsSetup] = None
    api_key: Annotated[SecretStr | None, NeedsSetup, Secret()] = None


class Plain(BaseModel):
    """Unmarked: its ``token`` is still the bearer."""

    base_url: str = ""
    token: SecretStr | None = None


class Tokenless(BaseModel):
    base_url: str = _URL


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _authorization(config: BaseModel, **kwargs: object) -> str | None:
    with respx.mock:
        route = respx.get(f"{_URL}/me").mock(return_value=httpx.Response(200, json={}))
        with connected_client(config, section="demo", http=_HTTP, **kwargs) as client:  # type: ignore[arg-type]
            client.get("/me")
    header: str | None = route.calls.last.request.headers.get("Authorization")
    return header


@pytest.mark.parametrize(
    ("config", "bearer"),
    [
        (Marked(base_url=_URL, token=SecretStr("t")), "Bearer t"),
        (Hooked(base_url=_URL, token=SecretStr("t"), webhook_secret=SecretStr("w")), "Bearer t"),
        (Keyed(base_url=_URL, api_key=SecretStr("k")), "Bearer k"),
        (Plain(base_url=_URL, token=SecretStr("p")), "Bearer p"),
    ],
    ids=["token", "token-over-another-secret", "the-only-secret", "unmarked-token"],
)
def test_the_bearer_defaults_to_the_token_else_the_only_secret(
    config: BaseModel, bearer: str
) -> None:
    assert _authorization(config, base_url=_URL) == bearer


def test_an_explicit_bearer_wins() -> None:
    config = Marked(base_url=_URL, token=SecretStr("t"))
    assert _authorization(config, base_url=_URL, bearer=SecretStr("other")) == "Bearer other"


@pytest.mark.parametrize(("token", "header"), [(None, None), (SecretStr("t"), "Bearer t")])
def test_a_client_that_needs_no_token_still_sends_one_it_has(
    token: SecretStr | None, header: str | None
) -> None:
    """awx's ``ping``: no token needed, but a configured one is still sent."""
    config = Marked(base_url=_URL, token=token)
    assert _authorization(config, base_url=_URL, require_token=False) == header


def test_a_missing_value_names_the_unset_marked_fields(_isolated_config: Path) -> None:
    _write(
        _isolated_config,
        "active: home\n"
        "profiles:\n"
        "  home: {}\n"
        "  work:\n"
        "    demo: {base_url: https://work.example.com, token_command: [pass, show, demo]}\n",
    )
    with pytest.raises(ConfigError) as caught:
        connected_client(Marked(), section="demo", base_url=None, http=_HTTP)

    error = caught.value
    assert str(error) == (
        "demo is not set up in profile 'home': demo.base_url and demo.token are not set"
    )
    assert error.hint is not None
    assert "profile 'work' sets them" in error.hint
    assert error.hint.endswith("run `untaped setup plan --only demo`")
    assert error.details["profiles"] == ["work"]


def test_a_missing_token_alone_is_named() -> None:
    with pytest.raises(ConfigError, match=r"^demo is not set up.*: demo\.token is not set$"):
        connected_client(Marked(base_url=_URL), section="demo", base_url=_URL, http=_HTTP)


@pytest.mark.parametrize(
    ("config", "kwargs", "named"),
    [
        (Plain(base_url=_URL), {"base_url": _URL}, "bearer= has no value"),
        (Plain(token=SecretStr("t")), {"base_url": " "}, "base_url= has no value"),
        (Plain(), {"base_url": ""}, "base_url= and bearer= have no value"),
        (Tokenless(), {"base_url": _URL}, "bearer= has no value"),
    ],
    ids=["unmarked-token", "blank-url", "both", "no-secret-at-all"],
)
def test_a_blank_value_from_an_unmarked_field_names_the_parameter(
    config: BaseModel, kwargs: dict[str, str], named: str
) -> None:
    with pytest.raises(ConfigError) as caught:
        connected_client(config, section="demo", http=_HTTP, **kwargs)  # type: ignore[arg-type]
    assert str(caught.value).startswith("demo is not set up")
    assert str(caught.value).endswith(f": {named}")
    assert (caught.value.hint or "").endswith("run `untaped setup plan --only demo`")


def test_a_model_without_a_secret_builds_a_tokenless_client_when_none_is_required() -> None:
    assert _authorization(Tokenless(), base_url=_URL, require_token=False) is None


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"required": ("base_url",)}, "required="),
        ({"required": ("base_url", "token")}, "required="),
        ({"bearer_token_field": "token"}, "bearer_token_field="),
        ({"base_url_field": "url", "required": ()}, "required= and base_url_field="),
    ],
    ids=["required", "required-as-its-old-default", "bearer_token_field", "two"],
)
def test_base_url_with_an_old_parameter_refuses(kwargs: dict[str, object], named: str) -> None:
    """Even an old parameter's former default counts as passed."""
    config = Marked(base_url=_URL, token=SecretStr("t"))
    with (
        pytest.warns(DeprecationWarning, match=r"connected_client\("),
        pytest.raises(TypeError, match=f"pass base_url= or {named}, not both"),
    ):
        connected_client(config, section="demo", base_url=_URL, http=_HTTP, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"bearer": "t"}, "bearer="),
        ({"require_token": True}, "require_token="),
        ({"require_token": False, "bearer": None}, "bearer= and require_token="),
    ],
    ids=["bearer", "require_token", "both"],
)
def test_a_new_parameter_without_base_url_refuses(kwargs: dict[str, object], named: str) -> None:
    """Without ``base_url=`` the old read would ignore them."""
    config = Marked(base_url=_URL, token=SecretStr("t"))
    with pytest.raises(TypeError, match=f"pass base_url= with {named}$"):
        connected_client(config, section="demo", http=_HTTP, **kwargs)  # type: ignore[arg-type]


def test_a_client_needing_no_token_does_not_name_it_as_unset() -> None:
    with pytest.raises(ConfigError) as caught:
        connected_client(Marked(), section="demo", base_url=None, require_token=False, http=_HTTP)
    assert str(caught.value).endswith(": demo.base_url is not set")


def test_a_passed_bearer_is_not_named_as_unset() -> None:
    with pytest.raises(ConfigError) as caught:
        connected_client(Marked(), section="demo", base_url="", bearer="t", http=_HTTP)
    assert str(caught.value).endswith(": demo.base_url is not set")


def test_leaving_base_url_out_keeps_the_old_read_and_warns() -> None:
    config = Plain(base_url=_URL, token=SecretStr("p"))
    with pytest.warns(DeprecationWarning, match="for demo without base_url=") as caught:
        assert _authorization(config) == "Bearer p"
    assert caught[0].filename == __file__, "the warning names the caller, not core"
    with (
        pytest.warns(DeprecationWarning, match="without base_url="),
        pytest.raises(ConfigError, match=r"demo\.token is not configured"),
    ):
        connected_client(Plain(base_url=_URL), section="demo", http=_HTTP)


def test_a_rejected_secret_names_its_own_auth_set() -> None:
    register_profile_settings("duo", Hooked)
    cause = HttpError("HTTP 401", status_code=401, url=f"{_URL}/hooks", system="duo")
    error = rejected_token_error("duo", "rejected", cause=cause, field="webhook_secret")
    assert error.hint == "run `untaped auth set duo.webhook_secret`"
    assert rejected_token_error("duo", "rejected").hint == "run `untaped auth set duo`"

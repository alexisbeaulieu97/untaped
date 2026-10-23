"""``connected_client`` takes the values it needs: ``base_url=``, ``bearer=``, ``verify=``.

``bearer`` is the token a ``Credential`` gave (bound to its server), or
``None`` for a client without one. A blank URL is "<section> is not set
up", naming the unset marked settings (or the parameter, for an unmarked
model); leaving ``base_url=`` out keeps the old read by field name and
warns; passing both kinds refuses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import httpx
import pytest
import respx
from pydantic import BaseModel, HttpUrl, SecretStr

from core_test_support.commands.support import compose, make_spec
from untaped.runtime.settings import HttpSettings
from untaped.sdk import BoundSecret, Credential, CredentialKey, NeedsSetup
from untaped.sdk.errors import ConfigError, HttpError
from untaped.sdk.network.http import connected_client, rejected_token_error

pytestmark = pytest.mark.usefixtures("_isolated_config")

_URL = "https://api.example.com"
_HTTP = HttpSettings()


class Server(CredentialKey):
    url: HttpUrl


class Marked(BaseModel):
    """The usual service: a marked server; its token is a declared credential."""

    server: Annotated[Server | None, NeedsSetup] = None


class Plain(BaseModel):
    """Unmarked: its ``token`` is still the bearer of the old read by name."""

    base_url: str = ""
    token: SecretStr | None = None


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
    ("bearer", "header"),
    [
        (None, None),
        ("t", "Bearer t"),
        (SecretStr("t"), "Bearer t"),
        (BoundSecret("t", origin=_URL, source="test"), "Bearer t"),
    ],
    ids=["none", "text", "secret", "bound"],
)
def test_the_bearer_is_what_the_caller_passes(bearer: object, header: str | None) -> None:
    assert _authorization(Marked(), base_url=_URL, bearer=bearer) == header


@pytest.mark.parametrize(
    ("origin", "says"),
    [
        ("https://other.example.com", r"with a credential bound to https://other\.example\.com"),
        (None, "this credential is not for HTTP"),
    ],
)
def test_a_token_bound_elsewhere_is_refused(origin: str | None, says: str) -> None:
    bearer = BoundSecret("t", origin=origin, source="test")
    with pytest.raises(ConfigError, match=says):
        connected_client(Marked(), section="demo", base_url=_URL, bearer=bearer, http=_HTTP)


def test_a_bound_token_is_sent_only_to_its_origin() -> None:
    bearer = BoundSecret("t", origin=_URL, source="test")
    with (
        respx.mock,
        connected_client(Marked(), section="demo", base_url=_URL, bearer=bearer, http=_HTTP) as c,
        pytest.raises(HttpError, match=r"it is bound to https://api\.example\.com"),
    ):
        c.get("https://elsewhere.example.com/me")


def test_a_missing_url_names_the_unset_marked_fields(_isolated_config: Path) -> None:
    _write(
        _isolated_config,
        "active: home\n"
        "profiles:\n"
        "  home: {}\n"
        "  work:\n"
        "    demo: {server: {url: https://work.example.com}}\n",
    )
    with pytest.raises(ConfigError) as caught:
        connected_client(Marked(), section="demo", base_url=None, http=_HTTP)

    error = caught.value
    assert str(error) == "demo is not set up in profile 'home': demo.server is not set"
    assert error.hint is not None
    assert "profile 'work' sets it" in error.hint
    assert error.hint.endswith("run `untaped setup plan --only demo`")
    assert error.details["profiles"] == ["work"]


class Bare(BaseModel):
    """Nothing marked and nothing the old fallback reads."""

    timeout: float = 1.0


def test_a_blank_url_of_an_unmarked_model_names_its_setting_else_the_parameter() -> None:
    with pytest.raises(ConfigError) as caught:
        connected_client(Plain(), section="demo", base_url=" ", http=_HTTP)
    assert str(caught.value).endswith(": demo.base_url is not set")
    with pytest.raises(ConfigError) as caught:
        connected_client(Bare(), section="demo", base_url=" ", http=_HTTP)
    assert str(caught.value).startswith("demo is not set up")
    assert str(caught.value).endswith(": base_url= has no value")
    assert (caught.value.hint or "").endswith("run `untaped setup plan --only demo`")


@pytest.mark.parametrize(
    ("verify", "expected"),
    [(True, True), (False, False)],
)
def test_verify_is_the_keys_tls(verify: bool, expected: bool) -> None:
    client = connected_client(Marked(), section="demo", base_url=_URL, verify=verify, http=_HTTP)
    with client:
        assert (client._client._transport._pool._ssl_context.verify_mode != 0) is expected  # type: ignore[union-attr]


def test_a_missing_ca_file_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="does not exist"):
        connected_client(
            Marked(), section="demo", base_url=_URL, verify=tmp_path / "ca.pem", http=_HTTP
        )


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
    with (
        pytest.warns(DeprecationWarning, match=r"connected_client\("),
        pytest.raises(TypeError, match=f"pass base_url= or {named}, not both"),
    ):
        connected_client(Marked(), section="demo", base_url=_URL, http=_HTTP, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"bearer": "t"}, "bearer="),
        ({"verify": False}, "verify="),
        ({"verify": True, "bearer": None}, "bearer= and verify="),
    ],
    ids=["bearer", "verify", "both"],
)
def test_a_new_parameter_without_base_url_refuses(kwargs: dict[str, object], named: str) -> None:
    """Without ``base_url=`` the old read would ignore them."""
    with pytest.raises(TypeError, match=f"pass base_url= with {named}$"):
        connected_client(Plain(base_url=_URL), section="demo", http=_HTTP, **kwargs)  # type: ignore[arg-type]


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


class DuoServer(CredentialKey):
    url: HttpUrl


class Duo(BaseModel):
    server: Annotated[DuoServer | None, NeedsSetup] = None


def test_a_rejected_credential_names_its_own_auth_login() -> None:
    token = Credential("duo", "token", key=DuoServer)
    hook = Credential("duo", "webhook_secret", key=DuoServer)
    compose(make_spec("duo", settings=Duo, credentials=(token, hook)))
    cause = HttpError("HTTP 401", status_code=401, url=f"{_URL}/hooks", system="duo")
    error = rejected_token_error("duo", "rejected", cause=cause, field="webhook_secret")
    assert error.hint == "run `untaped auth login duo webhook_secret`"
    assert error.fix_by == "user"
    assert rejected_token_error("duo", "rejected").hint == "run `untaped auth login duo`"


@pytest.mark.parametrize(
    ("http", "verify", "url", "sent"),
    [
        (HttpSettings(proxy="http://proxy.example.com:3128"), True, _URL, True),
        (HttpSettings(proxy="http://proxy.example.com:3128"), False, _URL, False),
        (
            HttpSettings(proxy="http://proxy.example.com:3128", verify_hostname=False),
            True,
            _URL,
            False,
        ),
        # root verify_ssl already reads as the key's verify_tls; a key saying true set it
        (HttpSettings(proxy="http://proxy.example.com:3128", verify_ssl=False), True, _URL, True),
        (
            HttpSettings(proxy="http://proxy.example.com:3128"),
            True,
            "http://plain.example.com",
            False,
        ),
    ],
)
def test_a_folder_proxy_carries_a_credential_only_over_verified_https(
    monkeypatch: pytest.MonkeyPatch, http: HttpSettings, verify: bool, url: str, sent: bool
) -> None:
    monkeypatch.setattr(
        "untaped.sdk.network.credentials.folder_leaves",
        lambda: {("http", "proxy"): "/work/.untaped.yml"},
    )

    def build() -> None:
        connected_client(
            Marked(), section="demo", base_url=url, bearer="tok", http=http, verify=verify
        ).close()

    if sent:
        build()
    else:
        with pytest.raises(ConfigError, match="won't send a credential through a folder's proxy"):
            build()


def test_a_folder_trust_setting_carries_no_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "untaped.sdk.network.credentials.folder_leaves",
        lambda: {("http", "ca_bundle"): "/work/.untaped.yml"},
    )

    with pytest.raises(ConfigError, match="won't send a credential under a trust setting"):
        connected_client(Marked(), section="demo", base_url=_URL, bearer="tok", http=_HTTP)


@pytest.mark.parametrize(("verify", "verify_ssl"), [(True, True), (None, False)])
def test_a_keys_client_takes_root_trust_without_verify_ssl(
    monkeypatch: pytest.MonkeyPatch, verify: bool | None, verify_ssl: bool
) -> None:
    """A key's TLS already holds root verify_ssl; a client given no key's TLS still applies it."""
    seen: list[HttpSettings] = []

    def trust(http: HttpSettings) -> bool:
        seen.append(http)
        return True

    monkeypatch.setattr("untaped.sdk.network.http.resolve_verify", trust)
    root = HttpSettings(verify_ssl=False, ca_bundle=Path("/etc/ca.pem"))

    connected_client(
        Marked(), section="demo", base_url=_URL, bearer="tok", http=root, verify=verify
    ).close()

    [used] = seen
    assert (used.verify_ssl, used.ca_bundle) == (verify_ssl, Path("/etc/ca.pem"))

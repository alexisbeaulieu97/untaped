"""``http`` resolves per-profile under the profiles layout.

``http`` (like ``ui``) is an ordinary per-profile setting: the base
(``config.yml``'s top level) is the shared layer and the picked profile
overrides it leaf-by-leaf.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from pydantic import BaseModel, SecretStr

from untaped.runtime.settings import get_settings, register_profile_settings
from untaped.sdk.network.http import connected_client


class _GithubSettings(BaseModel):
    token: SecretStr | None = None


def test_active_profile_http_overrides_default_base(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    register_profile_settings("github", _GithubSettings)
    _isolated_config.write_text(
        "format_version: 2\n"
        "untaped:\n  http:\n    proxy: http://base:3128\n    timeout_seconds: 30\n"
        "profiles:\n  work:\n"
        "    untaped:\n      http:\n        timeout_seconds: 5\n"
        "    github:\n      token: t\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UNTAPED_PROFILE", "work")
    get_settings.cache_clear()

    settings = get_settings()
    # ``default`` supplies the proxy (base layer); ``work`` overrides the timeout.
    assert settings.untaped.http.proxy == "http://base:3128"
    assert settings.untaped.http.timeout_seconds == 5.0
    assert settings.github.token is not None
    assert settings.github.token.get_secret_value() == "t"


class _PagedSettings(BaseModel):
    page_size: int = 10


class _ConnSettings(BaseModel):
    base_url: str | None = None
    token: SecretStr | None = None


@respx.mock
def test_connected_client_http_default_ignores_invalid_sibling_section(
    _isolated_config: Path,
) -> None:
    register_profile_settings("github", _GithubSettings)
    register_profile_settings("paged", _PagedSettings)
    _isolated_config.write_text(
        "format_version: 2\n"
        "untaped:\n  http:\n    timeout_seconds: 7\n"
        "paged:\n  page_size: notanint\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    config = _ConnSettings(base_url="https://example.test", token=SecretStr("t"))
    respx.get("https://example.test/user").mock(return_value=httpx.Response(200, json={}))
    with connected_client(config, section="conn", base_url=config.base_url) as client:
        client.get("/user")

    assert respx.calls.last.request.extensions["timeout"]["read"] == 7

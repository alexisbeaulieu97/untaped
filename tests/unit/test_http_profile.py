"""``http`` resolves per-profile under the profiles layout.

``http`` (like ``ui`` and ``log_level``) is an ordinary per-profile setting now:
``profiles.default`` is the shared base layer and the active profile overrides
it leaf-by-leaf. A bare top-level ``http:`` block is no longer special.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from untaped.settings import get_settings, register_profile_settings


class _GithubSettings(BaseModel):
    token: str | None = None


def test_http_resolves_from_active_profile(_isolated_config: Path) -> None:
    register_profile_settings("github", _GithubSettings)
    _isolated_config.write_text(
        "active: work\n"
        "profiles:\n"
        "  work:\n"
        "    http:\n      verify_ssl: false\n"
        "    github:\n      token: t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.http.verify_ssl is False
    assert settings.github.token == "t"


def test_active_profile_http_overrides_default_base(_isolated_config: Path) -> None:
    register_profile_settings("github", _GithubSettings)
    _isolated_config.write_text(
        "active: work\n"
        "profiles:\n"
        "  default:\n"
        "    http:\n      proxy: http://base:3128\n      verify_ssl: true\n"
        "  work:\n"
        "    http:\n      verify_ssl: false\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    settings = get_settings()
    # ``default`` supplies the proxy (base layer); ``work`` overrides verify_ssl.
    assert settings.http.proxy == "http://base:3128"
    assert settings.http.verify_ssl is False

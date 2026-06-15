"""Top-level ``http:`` resolves as an SDK global under the profiles layout.

Tools run under :class:`ProfilesSettingsLayout`, which keeps only profile
data in the effective view. ``http`` is an SDK-owned global (like ``ui``),
so a top-level ``http:`` block must still resolve — it is spliced in
regardless of the active layout.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from untaped import get_settings, register_profile_settings
from untaped.settings import register_settings_layout
from untaped.settings_layout import ProfilesSettingsLayout


class _GithubSettings(BaseModel):
    token: str | None = None


def test_top_level_http_resolves_under_profiles_layout(_isolated_config: Path) -> None:
    register_settings_layout(lambda: ProfilesSettingsLayout(), key="sdk:profiles")
    register_profile_settings("github", _GithubSettings)
    _isolated_config.write_text(
        "http:\n  verify_ssl: false\n"
        "active: work\n"
        "profiles:\n  work:\n    github:\n      token: t\n",
        encoding="utf-8",
    )
    get_settings.cache_clear()

    settings = get_settings()
    assert settings.http.verify_ssl is False
    assert settings.github.token == "t"

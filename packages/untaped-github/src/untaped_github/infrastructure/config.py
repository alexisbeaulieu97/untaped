"""Configuration struct for the GitHub package.

Decouples the package from :class:`untaped_core.Settings`. The only
place that reads ``Settings`` is the CLI composition root, which builds
a :class:`GithubConfig` once via :meth:`GithubConfig.from_settings` and
passes it into :class:`GithubClient`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, SecretStr

if TYPE_CHECKING:
    from untaped_core import Settings


class GithubConfig(BaseModel):
    """Connection configuration for a single GitHub target.

    Mirrors the shape of :class:`untaped_core.settings.GithubSettings` so
    the CLI can build one from settings in a single line, but lives in
    this package so adapters can depend on it without importing
    ``untaped_core``.
    """

    model_config = ConfigDict(frozen=True)

    base_url: str = "https://api.github.com"
    token: SecretStr | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> GithubConfig:
        """Build a ``GithubConfig`` from cross-cutting ``Settings``.

        Field-for-field bridge with
        :class:`untaped_core.settings.GithubSettings`. Keep them in
        sync — ``test_config.test_from_settings_field_set_matches_githubsettings``
        pins the inventory so a new field added on one side without the
        other fails CI loudly.
        """
        s = settings.github
        return cls(base_url=s.base_url, token=s.token)

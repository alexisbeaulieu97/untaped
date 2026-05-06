"""Configuration struct for the GitHub package.

Decouples the package from :class:`untaped_core.Settings`. The only
place that reads ``Settings`` is the CLI composition root, which builds
a :class:`GithubConfig` once and passes it into :class:`GithubClient`.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, SecretStr


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

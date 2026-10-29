"""The dotfiles settings (``dotfiles:`` in ``config.yml``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from untaped.sdk import Dir, key
from untaped_dotfiles.models import OsName


class DotfilesSettings(BaseModel):
    """User-tunable dotfiles profile settings."""

    model_config = ConfigDict(frozen=True)

    repos_dir: Path | None = Field(
        default=None,
        description=(
            "Where `dotfiles subscribe URL` clones repos (`<repos_dir>/NAME`), an absolute "
            "path. Unset: `plugins/dotfiles/clones/` under the home."
        ),
    )
    kept_dir: Path | None = Field(
        default=None,
        description=(
            "Where `apply`, `sync` and `remove` keep local files they replace, "
            "under `<repo>/<item>/<timestamp>/`; an absolute path. Unset: "
            "`plugins/dotfiles/kept/` under the home."
        ),
    )
    state_dir: Path | None = Field(
        default=None,
        description=(
            "Holds `status.json` and `attention`, the files a prompt segment reads; an "
            "absolute path. Unset: `plugins/dotfiles/prompt/` under the home."
        ),
    )
    tags: list[str] = Field(
        default_factory=list,
        description="This machine's tags, matched against `only` and `unless` in manifests.",
    )
    os: OsName | None = Field(
        default=None, description="This machine's OS for `os` filters; detected when unset."
    )


#: The clones ``dotfiles subscribe URL`` makes: ``dotfiles.repos_dir``, else under the home.
CLONES = Dir(at=key(DotfilesSettings).repos_dir)
#: The local files ``apply``, ``sync`` and ``remove`` replaced: ``dotfiles.kept_dir``.
KEPT = Dir(at=key(DotfilesSettings).kept_dir)
#: ``status.json`` and ``attention``, which a prompt segment reads: ``dotfiles.state_dir``.
PROMPT = Dir(at=key(DotfilesSettings).state_dir)

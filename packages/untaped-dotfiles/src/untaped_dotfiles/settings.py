"""The dotfiles settings (``dotfiles:`` in ``config.yml``): this machine's sources and choices.

Every setting describes this machine, so only the home's base sets it: a
project's file or a profile that set one is refused (``HomeOnly``). The
commands write them through ``plan_settings`` (``subscribe``, ``add``,
``remove``, ``unsubscribe``); ``config set`` reaches every entry too
(``dotfiles.sources.SOURCE.packages.PACKAGE.policy``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from untaped.sdk import key
from untaped.sdk.health import HomeOnly
from untaped.sdk.storage import Dir
from untaped_dotfiles.policy import Policy

#: An OS a machine runs and a package's ``os:`` names.
Os = Literal["macos", "linux", "windows"]
#: A plain input value a machine keeps for a package (a secret is never kept here).
type InputValue = str | int | float | bool


class ItemChoice(BaseModel):
    """This machine's exception for one item; an item not listed is on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = Field(True, description="false: the item is off on this machine.")
    policy: Policy | None = Field(None, description="Unset: the package's.")


class PackageChoice(BaseModel):
    """A package this machine turned on: the entry existing is what turns it on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: Policy | None = Field(
        None, description="This machine's policy; unset: the author's suggestion."
    )
    inputs: dict[str, InputValue] = Field(
        default_factory=dict, description="Answers and overrides of the package's inputs."
    )
    items: dict[str, ItemChoice] = Field(
        default_factory=dict, description="Exceptions only: an item not listed is on."
    )


class PackageSource(BaseModel):
    """Where packages come from: a git URL or a repo name, or a local folder."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str | None = Field(None, description="A git URL, or a repo name a repo source lists.")
    path: Path | None = Field(None, description="A local folder, read as it stands.")
    ref: str | None = Field(None, description="The branch to follow; default: the remote's.")
    packages: dict[str, PackageChoice] = Field(
        default_factory=dict, description="The packages this machine turned on."
    )

    @model_validator(mode="after")
    def _one_place(self) -> Self:
        if (self.url is None) == (self.path is None):
            raise ValueError("needs url or path")
        if self.path is not None and self.ref is not None:
            raise ValueError("ref applies to a url only")
        if self.path is not None and not self.path.expanduser().is_absolute():
            raise ValueError("path must be absolute or start with ~")
        return self


class DotfilesSettings(BaseModel):
    """This machine's dotfiles: its sources, the packages it turned on, and its OS and tags."""

    model_config = ConfigDict(frozen=True)

    sources: Annotated[dict[str, PackageSource], HomeOnly()] = Field(
        default_factory=dict,
        description="The sources this machine subscribed to, by name (`dotfiles subscribe`).",
    )
    tags: Annotated[list[str], HomeOnly()] = Field(
        default_factory=list,
        description="This machine's tags, matched against `only` and `unless` in packages.",
    )
    os: Annotated[Os | None, HomeOnly()] = Field(
        None, description="This machine's OS for `os` filters; detected when unset."
    )
    clones_dir: Path | None = Field(
        None,
        description=(
            "Where the managed clones of subscribed git sources go (`<clones_dir>/SOURCE`), "
            "an absolute path. Unset: `plugins/dotfiles/clones/` under the home."
        ),
    )
    kept_dir: Path | None = Field(
        None,
        description=(
            "Where `apply`, `remove` and `unsubscribe` keep local files they replace, under "
            "`<source>/<package>/<item>/<timestamp>/`; an absolute path. Unset: "
            "`plugins/dotfiles/kept/` under the home."
        ),
    )
    state_dir: Path | None = Field(
        None,
        description=(
            "Holds `status.json` and `attention`, the files a prompt segment reads; an "
            "absolute path. Unset: `plugins/dotfiles/prompt/` under the home."
        ),
    )


#: The managed clones of git sources: ``dotfiles.clones_dir``, else under the home.
CLONES = Dir(at=key(DotfilesSettings).clones_dir)
#: The local files ``apply``, ``remove`` and ``unsubscribe`` replaced: ``dotfiles.kept_dir``.
KEPT = Dir(at=key(DotfilesSettings).kept_dir)
#: ``status.json`` and ``attention``, which a prompt segment reads: ``dotfiles.state_dir``.
PROMPT = Dir(at=key(DotfilesSettings).state_dir)

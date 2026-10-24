"""Domain entities for the profile bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, Field

from untaped.runtime.profile_resolver import PickSource
from untaped.sdk.records import OutcomeRecord, Record, TableGlyph


@dataclass(frozen=True)
class StoredProfile:
    """One profile entry with its raw YAML data and metadata.

    ``data`` is the verbatim ``profiles.<name>`` block (no fallback merge);
    use cases that need the resolved view ask the repository for it.
    """

    name: str
    data: dict[str, Any]
    selected: bool
    """Whether the run reads it: in the pick, or the base when nothing is picked."""

    @property
    def key_count(self) -> int:
        """Number of leaf keys this profile sets (for the list table)."""
        return _count_leaves(self.data)


@dataclass(frozen=True)
class ProfileDeletePreview:
    """Non-secret summary shown before confirming profile deletion."""

    name: str
    top_level_keys: tuple[str, ...]


class Profile(Record, kind="untaped.profile"):
    """One row of ``profile list``."""

    name: str
    selected: Annotated[bool, TableGlyph(true="✓", false="")]
    keys: int = Field(description="Number of leaf keys the profile sets.")


class PickedProfile(BaseModel):
    """One profile of the run's pick."""

    name: str
    source: PickSource = Field(description="What picked it: `flag` (--profile) or `env`.")


class ProfilePick(Record, kind="untaped.profile_pick"):
    """``profile current``: the profiles the run layers over the base, in order."""

    name: str = Field(
        description="The pick as one `--profile` value: the names comma-joined, else `default`."
    )
    profiles: list[PickedProfile]


class ProfileView(Record, kind="untaped.profile_view"):
    """``profile show``: one profile's settings, or the run's pick merged."""

    name: str = Field(description="The profile, or the pick comma-joined.")
    selected: bool = Field(description="The run reads it.")
    raw: bool = Field(description="Only the keys the profile itself sets, not merged.")
    data: dict[str, Any] = Field(description="The settings, secrets shown as *** by default.")


class ProfileOutcome(OutcomeRecord, kind="untaped.profile_outcome"):
    """The result of ``profile create/delete/rename``.

    ``action`` is ``created``, ``deleted``, ``renamed``, or ``planned`` under
    ``--dry-run``. ``previous_name`` is set by ``rename`` and ``copied_from``
    by ``create --copy-from``.
    """

    name: str
    previous_name: str | None = None
    copied_from: str | None = None


def _count_leaves(data: dict[str, Any]) -> int:
    total = 0
    for value in data.values():
        if isinstance(value, dict):
            total += _count_leaves(value)
        else:
            total += 1
    return total

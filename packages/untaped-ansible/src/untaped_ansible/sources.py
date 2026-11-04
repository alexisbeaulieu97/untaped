"""Saved sources: the repos a plugin selects, and what a refresh scans in them.

Kept apart from :mod:`untaped_ansible.settings`, which every start imports: a
source holds the git plugin's ``RepoSelector``, loaded only when a command
reads sources.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from untaped.sdk import Key
from untaped.sdk.storage import StateRows
from untaped_git.api import RepoSelector

ALLOWED_REF_KINDS = ("heads", "tags")


class SourceDefinition(BaseModel):
    """A saved source: the repos a plugin selects, and what a refresh scans in them.

    ``selector`` is the plugin's own: one it issued (piped from its command) carries
    its record, which ansible never reads; one with only ``match`` patterns
    takes the plugin's listing by name.
    """

    model_config = ConfigDict(frozen=True)

    name: Annotated[str, Key]
    source: str
    selector: RepoSelector
    dependency_paths: list[str] = Field(default_factory=list)
    ref_kinds: list[str] = Field(default_factory=list)
    ref_patterns: list[str] = Field(default_factory=list)
    ref_scan_default: Literal["all", "default_branch"] | None = None

    @field_validator("dependency_paths", "ref_kinds", "ref_patterns")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return _dedupe_sorted(values)

    @model_validator(mode="after")
    def _validate_source(self) -> Self:
        issued = self.selector.source
        if issued is not None and issued.plugin != self.source:
            raise ValueError(f"the selector was issued by {issued.plugin}, not {self.source}")
        invalid_ref_kinds = sorted(set(self.ref_kinds) - set(ALLOWED_REF_KINDS))
        if invalid_ref_kinds:
            raise ValueError("ref-kind must be heads or tags")
        return self


#: Saved sources, one row per name. Managed by `ansible source` commands.
SOURCES = StateRows[SourceDefinition](omit_none=True)


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted(dict.fromkeys(values))

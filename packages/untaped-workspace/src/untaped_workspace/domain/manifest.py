"""Pydantic models for the per-workspace ``untaped.yml`` manifest."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Repo(BaseModel):
    """One repo declared in a workspace manifest."""

    model_config = ConfigDict(extra="forbid")

    url: str
    name: str
    branch: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _fill_default_name(cls, data: Any) -> Any:
        # Derive `name` from `url` before per-field validation runs, so the
        # field can be a plain `str` (not `str | None`) and callers can stop
        # asserting `repo.name is not None` everywhere.
        if isinstance(data, dict) and not data.get("name"):
            url = data.get("url")
            if isinstance(url, str) and url.strip():
                data = {**data, "name": derive_repo_name(url.strip())}
        return data

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("repo url cannot be empty")
        return v


class ManifestDefaults(BaseModel):
    """Workspace-wide defaults applied to repos that don't override them."""

    model_config = ConfigDict(extra="forbid")

    branch: str | None = None


class WorkspaceManifest(BaseModel):
    """The contents of ``<workspace-dir>/untaped.yml``."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    defaults: ManifestDefaults = Field(default_factory=ManifestDefaults)
    repos: list[Repo] = Field(default_factory=list)

    def repo_by_name(self, name: str) -> Repo | None:
        return next((r for r in self.repos if r.name == name), None)

    def repo_by_url(self, url: str) -> Repo | None:
        return next((r for r in self.repos if r.url == url), None)

    def find_repo(self, ident: str) -> Repo | None:
        """Find a repo by URL or by alias name."""
        return self.repo_by_url(ident) or self.repo_by_name(ident)

    def target_branch_for(self, repo: Repo) -> str | None:
        """Return the branch a repo should be on at clone time, per cascade.

        per-repo > workspace defaults > None (let git use remote HEAD).
        """
        return repo.branch or self.defaults.branch


_NAME_RE = re.compile(r"([^/]+?)(?:\.git)?/*$")


def derive_repo_name(url: str) -> str:
    """Derive a default local directory name from a git URL.

    Handles both SSH-style (``git@host:org/repo.git``) and URL-style
    (``https://host/org/repo.git``). Falls back to the last path segment.
    """
    # SSH form: git@host:org/repo.git → take "org/repo.git"
    if ":" in url and "://" not in url:
        ssh_path = url.split(":", 1)[1]
        match = _NAME_RE.search(ssh_path)
    else:
        parsed = urlparse(url)
        match = _NAME_RE.search(parsed.path or url)
    if match:
        return match.group(1)
    return url

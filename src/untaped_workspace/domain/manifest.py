"""Pydantic models for the per-workspace ``untaped.yml`` manifest."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DuplicateRepoError(ValueError):
    """Base class for duplicate-repo invariant failures.

    Subclasses carry the *incumbent* (the repo already in the manifest)
    so callers can format error messages without re-scanning. Subclassing
    ``ValueError`` keeps the existing
    ``@model_validator(mode="after")`` contract — pydantic wraps these
    into ``ValidationError`` at construction time just as it would a
    plain ``ValueError``.
    """

    def __init__(self, message: str, existing: Repo) -> None:
        super().__init__(message)
        self.existing = existing

    def __reduce__(self) -> tuple[Any, tuple[Repo]]:
        # ``Exception.__reduce__`` pickles via ``self.args``, which is
        # just the message string — unpickling would call the subclass
        # ``__init__`` with a ``str`` where ``Repo`` is expected and
        # crash. Round-trip via the incumbent instead so these can
        # safely cross process boundaries (foreach / sync workers).
        return type(self), (self.existing,)


class DuplicateRepoName(DuplicateRepoError):
    """A repo with the same `name` is already in the manifest."""

    def __init__(self, existing: Repo) -> None:
        super().__init__(f"duplicate repo name: {existing.name!r}", existing)


class DuplicateRepoUrl(DuplicateRepoError):
    """A repo with the same `url` is already in the manifest."""

    def __init__(self, existing: Repo) -> None:
        super().__init__(f"duplicate repo url: {existing.url!r}", existing)


class Repo(BaseModel):
    """One repo declared in a workspace manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

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

    model_config = ConfigDict(extra="forbid", frozen=True)

    branch: str | None = None


class WorkspaceManifest(BaseModel):
    """The contents of ``<workspace-dir>/untaped.yml``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str | None = None
    defaults: ManifestDefaults = Field(default_factory=ManifestDefaults)
    # ``tuple`` (not ``list``) so in-place mutation raises at runtime —
    # pydantic's ``frozen=True`` only blocks attribute reassignment.
    repos: tuple[Repo, ...] = ()

    @staticmethod
    def _first_duplicate(repos: Sequence[Repo]) -> DuplicateRepoError | None:
        # Sync and remove both key off the (name, url) pair: two repos
        # sharing either field would collide on disk or make `remove`
        # ambiguous. Single source of truth shared by the post-construct
        # validator and ``add_repo`` so both paths raise the same typed
        # exception in the same precedence.
        #
        # Precedence is **url before name** so re-adding an existing URL
        # surfaces as ``DuplicateRepoUrl`` ("already in workspace")
        # rather than ``DuplicateRepoName`` — the derived name *also*
        # collides in that case, but the user's correct mental model is
        # "this repo is already here," not "your name conflicts."
        seen_names: dict[str, Repo] = {}
        seen_urls: dict[str, Repo] = {}
        for repo in repos:
            if (incumbent := seen_urls.get(repo.url)) is not None:
                return DuplicateRepoUrl(incumbent)
            if (incumbent := seen_names.get(repo.name)) is not None:
                return DuplicateRepoName(incumbent)
            seen_urls[repo.url] = repo
            seen_names[repo.name] = repo
        return None

    @model_validator(mode="after")
    def _reject_duplicate_repos(self) -> WorkspaceManifest:
        if (err := self._first_duplicate(self.repos)) is not None:
            raise err
        return self

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

    def add_repo(self, repo: Repo) -> WorkspaceManifest:
        """Return a new manifest with ``repo`` appended.

        Raises ``DuplicateRepoUrl`` or ``DuplicateRepoName`` (in that
        precedence) if ``repo`` collides with an existing entry. Each
        carries the incumbent so callers can build CLI-facing messages
        without re-scanning the manifest.
        """
        # Pre-check before construction so the exception carries the
        # incumbent. Reuses ``_first_duplicate`` — the same helper the
        # validator runs on YAML loads — so both paths raise the same
        # typed exception in the same precedence.
        new_repos = (*self.repos, repo)
        if (err := self._first_duplicate(new_repos)) is not None:
            raise err
        return WorkspaceManifest(
            name=self.name,
            defaults=self.defaults,
            repos=new_repos,
        )

    def remove_repo(self, ident: str) -> tuple[WorkspaceManifest, Repo]:
        """Return ``(new_manifest, removed_repo)``.

        ``ident`` is matched against URL first then alias name (see
        ``find_repo``). Raises ``ValueError`` if nothing matches.
        """
        found = self.find_repo(ident)
        if found is None:
            raise ValueError(f"no repo matches {ident!r}")
        return (
            WorkspaceManifest(
                name=self.name,
                defaults=self.defaults,
                # `found` is a reference into `self.repos`, so identity
                # match is unambiguous (and cheaper than value equality).
                repos=tuple(r for r in self.repos if r is not found),
            ),
            found,
        )


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

"""Git ls-remote backed remote ref freshness probe.

Runs the git plugin's ``ls_remote`` once per repo with ``ansible.probe_parallel``;
the credentials come from the host's ``GitHost`` (github's for the GitHub
host), and annotated tags arrive peeled to their commits. The default branch is
the inventory's (the remote's ``HEAD`` is asked only when the inventory named
none): the ``git`` backend still expands sources through the GitHub REST
inventory, so private sources still need credentials; it only replaces the
probe transport.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

from untaped.sdk import bounded_map
from untaped_ansible.domain.payloads import (
    GitRef,
    ProbedRepo,
    ProbeFailure,
    ProbeReport,
    ProbeTarget,
)
from untaped_ansible.domain.repo_targets import remote_url_for
from untaped_ansible.errors import GitCacheError

if TYPE_CHECKING:
    from untaped_ansible.application.ports import LsRemoteGit

GIT_REF_PROBE_FAILURE_PREFIX = "git ref probe failed: "


class GitRemoteRefProbe:
    """Probe branch/tag heads for many repos using ``git ls-remote``."""

    def __init__(
        self,
        git: LsRemoteGit,
        *,
        clone_protocol: str,
        concurrency: int = 8,
    ) -> None:
        if clone_protocol not in {"https", "ssh"}:
            raise ValueError("clone_protocol must be 'https' or 'ssh'")
        if concurrency < 1 or concurrency > 32:
            raise ValueError("concurrency must be between 1 and 32")
        self._git = git
        self._clone_protocol = clone_protocol
        self._concurrency = concurrency

    def probe(
        self,
        repos: Sequence[ProbeTarget],
        *,
        kinds: Sequence[str],
        mode: Literal["all", "default_branch"] = "all",
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ProbeReport:
        if mode not in {"all", "default_branch"}:
            raise ValueError("mode must be 'all' or 'default_branch'")
        probed: dict[str, ProbedRepo] = {}
        failures: dict[str, ProbeFailure] = {}
        total = len(repos)
        done = 0

        def probe_one(target: ProbeTarget) -> ProbedRepo | ProbeFailure:
            return self._probe_one(target, kinds=kinds, mode=mode)

        def record(target: ProbeTarget, outcome: ProbedRepo | ProbeFailure) -> None:
            nonlocal done
            if isinstance(outcome, ProbeFailure):
                failures[target.full_name] = outcome
            else:
                probed[target.full_name] = outcome
            done += 1
            if on_progress is not None:
                on_progress(done, total)

        bounded_map(probe_one, repos, concurrency=self._concurrency, on_each=record)
        return ProbeReport(repos=probed, failures=failures)

    def _probe_one(
        self,
        target: ProbeTarget,
        *,
        kinds: Sequence[str],
        mode: Literal["all", "default_branch"],
    ) -> ProbedRepo | ProbeFailure:
        url = remote_url_for(target, self._clone_protocol)
        try:
            if target.default_branch == "HEAD":
                branch = self._git.default_branch(url)
                if branch is not None:
                    target = target.model_copy(update={"default_branch": branch})
            refs = self._git.ls_remote(url, patterns=_patterns_for(target, kinds=kinds, mode=mode))
        except GitCacheError as exc:
            reason = str(exc) or type(exc).__name__
            return ProbeFailure(
                kind="git", reason=f"{GIT_REF_PROBE_FAILURE_PREFIX}{reason}", category=exc.category
            )
        return _probed(refs, target=target, kinds=kinds, mode=mode)


def _patterns_for(
    target: ProbeTarget,
    *,
    kinds: Sequence[str],
    mode: Literal["all", "default_branch"],
) -> list[str]:
    patterns = ["HEAD"]
    if mode == "default_branch":
        if target.default_branch and target.default_branch != "HEAD":
            patterns.append(f"refs/heads/{target.default_branch}")
        return patterns
    kind_set = set(kinds)
    if "heads" in kind_set:
        patterns.append("refs/heads/*")
    if "tags" in kind_set:
        patterns.append("refs/tags/*")
    return patterns


def _probed(
    refs: dict[str, str],
    *,
    target: ProbeTarget,
    kinds: Sequence[str],
    mode: Literal["all", "default_branch"],
) -> ProbedRepo:
    default_branch = target.default_branch
    if mode == "default_branch":
        ref = _default_branch_ref(default_branch, refs)
        return ProbedRepo(default_branch=default_branch, refs=(ref,) if ref is not None else ())

    selected: list[GitRef] = []
    kind_set = set(kinds)
    for kind in ("heads", "tags"):
        if kind in kind_set:
            prefix = f"refs/{kind}/"
            selected.extend(
                GitRef(kind=kind, name=name.removeprefix(prefix), sha=sha)
                for name, sha in refs.items()
                if name.startswith(prefix)
            )
    return ProbedRepo(
        default_branch=default_branch,
        refs=tuple(sorted(selected, key=lambda ref: (ref.kind, ref.name))),
    )


def _default_branch_ref(default_branch: str, refs: dict[str, str]) -> GitRef | None:
    full_ref = f"refs/heads/{default_branch}"
    sha = refs.get(full_ref) or refs.get("HEAD")
    if sha is None:
        return None
    return GitRef(kind="heads", name=default_branch, sha=sha)

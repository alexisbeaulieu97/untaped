"""Local git facts for ``awx test`` and ``awx apply``: the pushed HEAD branch.

``--scm-branch HEAD`` resolves to the current branch as named on its remote,
and ``--source-ref HEAD`` needs the same guarantee. AWX checks out what the
remote has, so HEAD is only usable once pushed: the branch needs an upstream
whose remote ref points at HEAD's commit.
"""

from __future__ import annotations

from pathlib import Path

from untaped.capability_api import ConfigError, GitCommandError, q, run_git

_TIMEOUT = 30.0


def pushed_branch(cwd: Path | None = None, *, flag: str = "--scm-branch") -> str:
    """The upstream branch name of the repository at ``cwd`` whose tip is HEAD.

    ``flag`` is the option that asked for ``HEAD``; errors name it.
    """
    try:
        return _pushed_branch(cwd, flag)
    except GitCommandError as exc:
        raise ConfigError(f"{flag} HEAD: {exc}") from exc


def _pushed_branch(cwd: Path | None, flag: str) -> str:
    head = _git(cwd, "rev-parse", "HEAD")
    # The full ref: ``--short`` answers ``heads/x`` when a tag ``x`` exists.
    local_ref = _git(cwd, "symbolic-ref", "--quiet", "HEAD", check=False)
    if not local_ref:
        raise ConfigError(f"HEAD is detached; pass {flag} a branch, tag or commit")
    branch = local_ref.removeprefix("refs/heads/")
    upstream = _git(
        cwd,
        "for-each-ref",
        "--format=%(upstream:remotename)%00%(upstream:remoteref)",
        local_ref,
    )
    remote, _, ref = upstream.partition("\0")
    if not remote or not ref:
        raise ConfigError(f"branch {q(branch)} has no upstream; push it with git push -u")
    name = ref.removeprefix("refs/heads/")
    if remote == ".":
        raise ConfigError(
            f"branch {q(branch)} tracks local branch {name}; push it with git push -u"
        )
    listed = run_git(
        ["ls-remote", remote, ref], cwd=cwd, timeout=_TIMEOUT, capture=True, retry_transient=True
    ).text.split()
    if not listed:
        raise ConfigError(f"{remote} has no {name}; push {branch} first")
    if listed[0] != head:
        raise ConfigError(
            f"HEAD {head[:12]} is not pushed: {remote} {name} is at {listed[0][:12]}; "
            f"push {branch} first"
        )
    return name


def _git(cwd: Path | None, *args: str, check: bool = True) -> str:
    result = run_git(list(args), cwd=cwd, timeout=_TIMEOUT, capture=True, check=check)
    return result.text.strip() if result.returncode == 0 else ""

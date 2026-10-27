"""The git plugin's doctor rows: is git new enough for the repo store, and is the store well.

``Git`` holds each check as an attribute, so its row is ``git.<attribute>``
in kebab case (``git.repo-store``); a body reads its id from its block.
"""

from __future__ import annotations

from untaped.sdk import DoctorCheck, DoctorResult, PluginContext, find_program, plural, size_text
from untaped_git.version import below_floor, floor_text, git_version, version_text


def _version(_ctx: PluginContext) -> DoctorResult:
    """``git.version``: fails below the repo store's floor, warns when git is missing."""
    check = VERSION.id
    if find_program("git") is None:
        return DoctorResult(
            id=check, ok=True, warn=True, detail="`git` not found on PATH; the repo store will fail"
        )
    version = git_version()
    if version is None:
        return DoctorResult(id=check, ok=True, warn=True, detail="could not read `git --version`")
    if below_floor(version):
        return DoctorResult(
            id=check,
            ok=False,
            detail=(
                f"git {version_text(version)} is older than {floor_text()}, which the repo "
                f"store needs; install git {floor_text()} or newer"
            ),
        )
    return DoctorResult(
        id=check, ok=True, detail=f"git {version_text(version)} (floor {floor_text()})"
    )


def _repo_store(_ctx: PluginContext) -> DoctorResult:
    """``git.repo-store``: warns about paused maintenance and interrupted releases; never fails.

    No fix: nothing untaped runs clears a ``gc.log`` (git retries after a
    day), and an interrupted release finishes on the repo's next use.
    """
    check = REPO_STORE.id
    from untaped_git.report import store_report  # noqa: PLC0415
    from untaped_git.settings import store_root  # noqa: PLC0415

    report = store_report(store_root(), version=None)
    problems = []
    if report.gc_log:
        problems.append(
            f"{plural(len(report.gc_log), 'repo')} with gc.log (auto maintenance paused)"
        )
    if report.unowned.repos:
        problems.append(
            f"{plural(report.unowned.repos, 'unowned repo')} "
            "(an interrupted release; finished on next use)"
        )
    if problems:
        return DoctorResult(id=check, ok=True, warn=True, detail="; ".join(problems))
    return DoctorResult(
        id=check,
        ok=True,
        detail=f"{plural(report.repos, 'repo')}, {size_text(report.size_bytes)}",
    )


VERSION = DoctorCheck(title="git version", run=_version)
REPO_STORE = DoctorCheck(title="git store", run=_repo_store)

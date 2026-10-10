"""The git plugin's doctor rows: is git new enough for the repo store, and is the store well."""

from __future__ import annotations

import shutil

from untaped.sdk import DoctorResult, PluginContext, plural, size_text
from untaped_git.infrastructure.version import below_floor, floor_text, git_version, version_text

_ID = "git.version"


def version_check(_ctx: PluginContext) -> DoctorResult:
    """``git.version``: fails below the repo store's floor, warns when git is missing."""
    if shutil.which("git") is None:
        return DoctorResult(
            id=_ID, ok=True, warn=True, detail="`git` not found on PATH; the repo store will fail"
        )
    version = git_version()
    if version is None:
        return DoctorResult(id=_ID, ok=True, warn=True, detail="could not read `git --version`")
    if below_floor(version):
        return DoctorResult(
            id=_ID,
            ok=False,
            detail=(
                f"git {version_text(version)} is older than {floor_text()}, which the repo "
                f"store needs; install git {floor_text()} or newer"
            ),
        )
    return DoctorResult(
        id=_ID, ok=True, detail=f"git {version_text(version)} (floor {floor_text()})"
    )


def store_check(_ctx: PluginContext) -> DoctorResult:
    """``git.store``: warns about paused maintenance and interrupted releases; never fails.

    No fix: nothing untaped runs clears a ``gc.log`` (git retries after a
    day), and an interrupted release finishes on the repo's next use.
    """
    from untaped_git.infrastructure.report import store_report  # noqa: PLC0415
    from untaped_git.settings import git_settings  # noqa: PLC0415

    report = store_report(git_settings().store_dir.expanduser(), version=None)
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
        return DoctorResult(id=_STORE_ID, ok=True, warn=True, detail="; ".join(problems))
    return DoctorResult(
        id=_STORE_ID,
        ok=True,
        detail=f"{plural(report.repos, 'repo')}, {size_text(report.size_bytes)}",
    )


_STORE_ID = "git.store"

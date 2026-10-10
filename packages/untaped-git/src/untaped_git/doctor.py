"""The git plugin's doctor row: is the installed git new enough for the repo store."""

from __future__ import annotations

import shutil

from untaped.sdk import DoctorResult, PluginContext
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

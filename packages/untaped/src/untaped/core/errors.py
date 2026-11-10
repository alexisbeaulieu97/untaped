"""Core's own errors, beside the SDK's (:mod:`untaped.sdk.errors`) that every plugin raises."""

from __future__ import annotations

from untaped.sdk.errors import System, UntapedError


class SurfaceError(UntapedError):
    """Something the surface walker can't classify: generation stops rather than skip it."""

    system = System.UNTAPED

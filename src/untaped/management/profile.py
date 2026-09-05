"""Root ``untaped profile …`` command group (Wave 1.4).

Converts the per-tool profile surface (:mod:`untaped.profile.app`) to a root
command by pure reuse: profiles live in the shared ``profiles`` layout, so
no resolution rule differs at the root. The only tool-specific detail of the
per-tool group is the empty-state hint, which names the given ``command`` —
the unified executable at the root.
"""

from __future__ import annotations

from cyclopts import App

from untaped.profile.app import build_profile_app


def build_root_profile_app(*, command: str) -> App:
    """Return the root ``profile`` command group naming ``command`` in hints."""
    return build_profile_app(command)


__all__ = ["build_root_profile_app"]

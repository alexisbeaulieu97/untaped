"""What a run is configured with beyond its flags: the profile's ``lint.ignore``.

``#641`` adds the repository's ``.untaped/lint.yml`` here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LintConfig:
    """The suppressions a run starts from, each ``<rule>[:<target-glob>]``."""

    ignore: tuple[str, ...] = ()

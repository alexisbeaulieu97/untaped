"""11.x Cyclopts option types for a plugin lint command not yet moved (``untaped awx lint``).

They spell what :class:`untaped_lint.commands._lint.Findings` holds; they go
when awx moves to the 12.0 shape.
"""

from __future__ import annotations

from typing import Annotated

from cyclopts import Parameter

SelectOption = Annotated[
    list[str] | None,
    Parameter(
        name="--select",
        negative="",
        help="Report only these rules (repeatable), as lint.error-case.",
    ),
]
IgnoreOption = Annotated[
    list[str] | None,
    Parameter(
        name="--ignore",
        negative="",
        help="Leave out a rule (repeatable), everywhere or where the target matches: RULE[:GLOB].",
    ),
]
StrictOption = Annotated[
    bool,
    Parameter(name="--strict", negative="", help="Fail (exit 3) on warnings too."),
]

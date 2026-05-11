"""Tab-completion callbacks for resource names.

Each callback takes the current incomplete token and returns matching
names from a (cached) AWX query. We keep these defensive — completion
must never raise — so any error returns an empty list.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

from untaped_awx.infrastructure.spec import AwxResourceSpec


def names_for(spec: AwxResourceSpec) -> Callable[[str], Iterator[str]]:
    """Return a Typer ``autocompletion`` callback for ``spec``'s names."""

    def _complete(incomplete: str) -> Iterator[str]:
        try:
            # Lazy imports: hoisting would force every `from untaped_awx.cli.commands
            # import app` (i.e. every `untaped --help`) to pay for application +
            # _context + httpx, just so a tab-press could be served quickly.
            from untaped_awx.application import ListResources  # noqa: PLC0415
            from untaped_awx.cli._context import open_context  # noqa: PLC0415

            with open_context() as ctx:
                use = ListResources(ctx.repo)
                for record in use(
                    spec,
                    search=incomplete or None,
                    limit=20,
                ):
                    name = record.get("name")
                    if isinstance(name, str):
                        yield name
        except Exception:
            return

    return _complete

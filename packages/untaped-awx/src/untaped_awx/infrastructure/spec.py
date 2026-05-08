"""Transport-aware extension of the domain :class:`ResourceSpec`.

:class:`AwxResourceSpec` adds the fields the framework needs to actually
talk to AWX (``api_path``) and to wire the CLI (``cli_name``,
``commands``, ``list_columns``). Use cases in ``application/`` depend
on :class:`ResourceSpec` (the domain type); concrete instances declared
here satisfy that interface structurally via inheritance.
"""

from __future__ import annotations

from untaped_awx.domain import CommandName, ResourceSpec


class AwxResourceSpec(ResourceSpec):
    """:class:`ResourceSpec` plus AWX REST + CLI wiring."""

    cli_name: str
    api_path: str
    list_columns: tuple[str, ...] = ()
    commands: tuple[CommandName, ...] = ("list", "get", "save", "apply")


def awx_api_path(spec: ResourceSpec) -> str:
    """Narrow a domain :class:`ResourceSpec` to :class:`AwxResourceSpec`
    and return its ``api_path``.

    Tripwire — every spec routed through :class:`AwxResourceCatalog` is
    constructed as :class:`AwxResourceSpec`. If a future catalog member
    breaks that invariant, this fails loudly at the narrowing site
    rather than at the next HTTP call.
    """
    if not isinstance(spec, AwxResourceSpec):
        raise TypeError(f"AwxResourceSpec required, got {type(spec).__name__}")
    return spec.api_path

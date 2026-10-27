"""The host listing behind ``untaped git hosts``: who supplies credentials where."""

from __future__ import annotations

from collections.abc import Callable

from untaped.sdk import ConfigError
from untaped.sdk.contracts import Ok, gather
from untaped_git.hosts import GitHost
from untaped_git.records import Host


def list_hosts(
    *, helpers_for: Callable[[str], list[str]], missing_helper: str | None
) -> list[Host]:
    """One row per host a ``GitHost`` provider names, sorted by host.

    A host two providers name lists both (the rank decides between them);
    ``credential`` says whether any of them has one for the host's root URL.
    ``helpers_for`` names the user's own credential helpers git asks first for a host.
    """
    try:
        homes = gather(GitHost.home)()
    except ConfigError:  # no provider is ready
        return []
    plugins: dict[str, list[str]] = {}
    for answer in homes:
        if isinstance(answer, Ok) and isinstance(answer.value, str) and answer.value:
            plugins.setdefault(answer.value.lower(), []).append(answer.plugin)
    rows = []
    for host, names in sorted(plugins.items()):
        rows.append(
            Host(
                host=host,
                plugins=names,
                credential=_has_credential(f"https://{host}/", names),
                helpers_first=helpers_for(host),
                missing_helper=missing_helper,
            )
        )
    return rows


def _has_credential(url: str, plugins: list[str]) -> bool:
    try:
        answers = gather(GitHost.credential, plugins=plugins)(url)
    except ConfigError:
        return False
    return any(isinstance(answer, Ok) and answer.value is not None for answer in answers)

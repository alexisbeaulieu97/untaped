"""The ``GitHost`` contract: which plugin supplies credentials for a Git host.

The git plugin owns it; a forge plugin (github, gitlab…) fills it for the
host its own settings name. :func:`resolve_host` is how untaped picks the
provider for a URL: the plugins whose ``home()`` is the URL's host are the
candidates, and only they are asked for credentials and proxy; none means
plain git (the user's own git config answers); several are decided by their
rank for ``credential`` (for ``proxy`` on an ssh URL, where no credential is
asked), and an unranked tie is a configuration error (exit 4) naming them and
the rank command. Credentials are asked only for ``https://`` URLs, so an ssh
remote never runs anyone's token command.

Core never imports this module: the repo store asks it itself, so consumers
never see a credential.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr

from untaped.contracts import Answers, Contract, Failed, Ok, gather
from untaped.sdk import ConfigError, experimental
from untaped_git.domain.url import url_host


class Credential(BaseModel):
    """A username and password (or token) for one HTTPS remote."""

    model_config = ConfigDict(frozen=True)

    username: str
    password: SecretStr


@experimental
class GitHost(Contract, shell=False):
    """A plugin that knows a Git host: its name, and credentials and proxy for its URLs.

    untaped asks ``credential`` and ``proxy`` only of the providers whose
    ``home()`` is the URL's host.
    """

    @abstractmethod
    def home(self) -> str | None:
        """The host this provider supplies credentials for, from config alone."""

    @abstractmethod
    def credential(self, url: str) -> Credential | None:
        """Credentials for the full ``url`` (per-organisation tokens), or ``None``."""

    def proxy(self, url: str) -> str | None:
        """The proxy URL for ``url``, or ``None`` for the user's own git config."""
        raise NotImplementedError

    @classmethod
    def for_url(cls, url: str) -> HostAuth | None:
        """The provider for ``url``'s host and its answers; ``None`` means plain git."""
        return resolve_host(url)


@dataclass(frozen=True, slots=True)
class HostAuth:
    """The provider chosen for a URL, with its credential (https only) and proxy."""

    plugin: str
    credential: Credential | None
    proxy: str | None


def resolve_host(url: str) -> HostAuth | None:
    """Choose the ``GitHost`` provider for ``url``; see the module docstring."""
    host = url_host(url)
    if host is None:
        return None
    homes = _ask(GitHost.home)
    if homes is None:
        return None
    candidates = [
        answer.plugin
        for answer in homes
        if isinstance(answer, Ok) and isinstance(answer.value, str) and answer.value.lower() == host
    ]
    if not candidates:
        return None
    https = url.startswith("https://")
    credentials = _ask(GitHost.credential, url, among=candidates) if https else None
    proxies = _ask(GitHost.proxy, url, among=candidates)
    chosen = _choose(host, candidates, credentials or proxies)
    return HostAuth(chosen, _answer(credentials, chosen), _answer(proxies, chosen))


def _ask(method: Any, *args: str, among: list[str] | None = None) -> Answers[Any] | None:
    try:
        return gather(method, plugins=among)(*args)
    except ConfigError:  # no provider is ready: plain git
        return None


def _choose(host: str, candidates: list[str], answers: Answers[Any] | None) -> str:
    """The one candidate, else the best ranked for the method that decides (``answers``)."""
    if len(candidates) == 1:
        return candidates[0]
    method = answers.method if answers is not None else "credential"
    if answers is not None:
        ranks = {a.plugin: a.rank for a in answers if a.plugin in candidates and a.rank is not None}
        if ranks:
            return min(ranks, key=lambda plugin: ranks[plugin])
    named = ", ".join(candidates)
    raise ConfigError(
        f"{named} all supply credentials for {host}; rank them to choose one",
        system="git",
        hint=f"untaped plugin rank git.git_host {method} {' '.join(candidates)}",
        details={"contract": "git.git_host", "method": method, "providers": candidates},
    )


def _answer[R](answers: Answers[R] | None, plugin: str) -> R | None:
    if answers is None:
        return None
    for answer in answers:
        if answer.plugin != plugin:
            continue
        if isinstance(answer, Failed):
            raise answer.error
        if isinstance(answer, Ok):
            return answer.value
    return None

"""Repository target helpers shared by source refresh backends."""

from __future__ import annotations

from untaped_ansible.domain.payloads import ProbeTarget
from untaped_git.api import repo_url_parts


def remote_url_for(target: ProbeTarget, clone_protocol: str) -> str:
    """The Git remote URL of a source repository: its URL, or its SSH form for ``ssh``.

    Without the provider's own SSH URL, the SSH form is ``git@HOST:PATH.git``
    from the stored URL; a URL with no host (a local path) is used as it is.
    """
    if clone_protocol != "ssh":
        return target.url
    if target.ssh_url:
        return target.ssh_url
    host, segments = repo_url_parts(target.url)
    if host is None or not segments:
        return target.url
    return f"git@{host}:{'/'.join(segments)}.git"

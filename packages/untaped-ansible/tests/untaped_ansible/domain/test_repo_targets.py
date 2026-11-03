"""A source repository's remote URL comes from its stored URL, in the protocol asked for."""

from __future__ import annotations

import pytest

from untaped_ansible.domain.payloads import ProbeTarget
from untaped_ansible.domain.repo_targets import remote_url_for

_URL = "https://gitlab.example.com/infra/platform/base.git"


def _target(**overrides: str) -> ProbeTarget:
    return ProbeTarget(
        full_name="infra/platform/base", default_branch="main", url=_URL, **overrides
    )


def test_https_is_the_stored_url() -> None:
    assert remote_url_for(_target(ssh_url="git@x:y.git"), "https") == _URL


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"ssh_url": "ssh://git@gitlab.example.com:2222/infra/platform/base.git"},
         "ssh://git@gitlab.example.com:2222/infra/platform/base.git"),
        ({}, "git@gitlab.example.com:infra/platform/base.git"),
    ],
)  # fmt: skip
def test_ssh_is_the_provider_s_ssh_url_or_the_scp_form_of_the_stored_one(
    overrides: dict[str, str], expected: str
) -> None:
    assert remote_url_for(_target(**overrides), "ssh") == expected


def test_a_local_path_is_used_as_it_is() -> None:
    target = ProbeTarget(full_name="acme/site", default_branch="main", url="/srv/git/site.git")

    assert remote_url_for(target, "ssh") == "/srv/git/site.git"

"""Tests for the state-backed alias and source repositories rejecting bad state."""

from __future__ import annotations

import pytest

from ansible_test_support import running, state_path, write_state
from untaped.sdk import ConfigError
from untaped_ansible.infrastructure import AliasRepository, SourceRepository


@pytest.mark.parametrize(
    ("repository", "state", "message"),
    [
        (AliasRepository, {"aliases": {"common": 123}}, "`aliases` must be a list of mappings"),
        (
            AliasRepository,
            {"aliases": [{"alias": "common"}]},
            "ansible.aliases row common in {path} is damaged: repo: Field required",
        ),
        (SourceRepository, {"sources": {"name": "prod"}}, "`sources` must be a list of mappings"),
        (
            SourceRepository,
            {"sources": [{"name": "prod"}]},
            "ansible.sources row prod in {path} is damaged: "
            "source requires --org, --team, or --repo",
        ),
    ],
)
def test_repositories_reject_malformed_state(
    repository: type[AliasRepository | SourceRepository], state: dict[str, object], message: str
) -> None:
    write_state(state)

    with running(), pytest.raises(ConfigError) as raised:
        repository().entries()
    assert message.format(path=state_path()) in str(raised.value)
    assert str(state_path()) in str(raised.value)

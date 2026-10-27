"""The saved sources and aliases: ansible's state rows refuse a damaged ``state.yml``."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ansible_test_support import running, state_path, write_state
from untaped.sdk import ConfigError
from untaped_ansible.settings import SOURCES, alias_map


@pytest.mark.parametrize(
    ("read", "state", "message"),
    [
        (alias_map, {"aliases": {"common": 123}}, "`aliases` must be a list of mappings"),
        (
            alias_map,
            {"aliases": [{"alias": "common"}]},
            "ansible.aliases row common in {path} is damaged: repo: Field required",
        ),
        (SOURCES.all, {"sources": {"name": "prod"}}, "`sources` must be a list of mappings"),
        (
            SOURCES.all,
            {"sources": [{"name": "prod"}]},
            "ansible.sources row prod in {path} is damaged: "
            "source requires --org, --team, or --repo",
        ),
    ],
)
def test_damaged_state_is_refused_naming_the_file(
    read: Callable[[], object], state: dict[str, object], message: str
) -> None:
    write_state(state)

    with running(), pytest.raises(ConfigError) as raised:
        read()
    assert message.format(path=state_path()) in str(raised.value)
    assert str(state_path()) in str(raised.value)

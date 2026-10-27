"""``ansible source-alias``: map a role or Galaxy name to its repo."""

from __future__ import annotations

import json
from pathlib import Path

from ansible_test_support import read_state, run, use_config


def test_source_alias_set_list_remove_updates_state(tmp_path: Path, monkeypatch) -> None:
    use_config(tmp_path, monkeypatch)

    assert run("source-alias", "set", "common", "acme/common").exit_code == 0

    listed = run("source-alias", "list", "--format", "json")
    assert json.loads(listed.stdout) == [{"alias": "common", "repo": "acme/common"}]

    assert run("source-alias", "remove", "common", "--yes").exit_code == 0
    assert "aliases" not in read_state()


def test_source_alias_set_warns_that_saved_sources_need_refresh(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "prod", "orgs": ["acme"]}]})

    result = run("source-alias", "set", "common", "acme/common")

    assert result.exit_code == 0, result.output
    assert "run `untaped ansible source refresh prod`" in result.stderr


def test_the_refresh_hint_quotes_a_source_name_a_shell_would_split(
    tmp_path: Path, monkeypatch
) -> None:
    use_config(tmp_path, monkeypatch, {"sources": [{"name": "my prod", "orgs": ["acme"]}]})

    result = run("source-alias", "set", "common", "acme/common")

    assert result.exit_code == 0, result.output
    assert "run `untaped ansible source refresh 'my prod'`" in result.stderr

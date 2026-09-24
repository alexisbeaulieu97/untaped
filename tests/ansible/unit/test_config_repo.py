"""Tests for the state-backed alias and source repositories rejecting bad state."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from untaped.capabilities.ansible.infrastructure import AliasRepository, SourceRepository
from untaped.capability_api import ConfigError


@pytest.mark.parametrize(
    ("repository", "state", "message"),
    [
        (AliasRepository, {"aliases": ["common"]}, r"`ansible\.aliases` must be a mapping"),
        (AliasRepository, {"aliases": {"common": 123}}, r"`ansible\.aliases` must be a string map"),
        (
            SourceRepository,
            {"sources": {"name": "prod"}},
            r"`ansible\.sources` must be a list of mappings",
        ),
        (
            SourceRepository,
            {"sources": [{"name": "prod"}]},
            r"invalid source 'prod': Value error, source requires --org, --team, or --repo",
        ),
    ],
)
def test_repositories_reject_malformed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    repository: type[AliasRepository | SourceRepository],
    state: dict[str, object],
    message: str,
) -> None:
    (tmp_path / "state.yml").write_text(yaml.safe_dump({"ansible": state}), encoding="utf-8")
    monkeypatch.setenv("UNTAPED_CONFIG", str(tmp_path / "config.yml"))

    with pytest.raises(ConfigError, match=message):
        repository().entries()

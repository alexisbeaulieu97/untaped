"""Validation rules of the Ansible settings and source models.

Defaults are pinned by the generated ``docs/reference/config.md`` (see
``tests/repo/test_docs.py``), so only the rejection rules live here.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from untaped.deprecated_keys import key_mappings
from untaped.sdk import get_config_section
from untaped_ansible.settings import AnsibleSettings, SourceDefinition

_SOURCE = {"name": "prod", "repos": ["acme/site"]}


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceDefinition, {**_SOURCE, "ref_scan_default": "main"}),
        (AnsibleSettings, {"ref_scan_default": "main"}),
        (AnsibleSettings, {"git_fetch_parallel": 0}),
        (AnsibleSettings, {"git_fetch_parallel": 33}),
        (AnsibleSettings, {"probe_parallel": 0}),
        (AnsibleSettings, {"probe_parallel": 33}),
        (AnsibleSettings, {"source_refresh_backend": "mercurial"}),
        (AnsibleSettings, {"source_refresh_repo_batch_size": 0}),
        (AnsibleSettings, {"source_refresh_rate_limit_floor": -1}),
        (AnsibleSettings, {"stale_after_seconds": -1}),
    ],
)
def test_models_reject_out_of_range_values(model: type[BaseModel], values: dict[str, Any]) -> None:
    field = next(key for key in values if key not in _SOURCE)

    with pytest.raises(ValidationError, match=field):
        model(**values)


def test_source_definition_accepts_per_source_ref_scan_default_and_tag_only_scans() -> None:
    source = SourceDefinition(**_SOURCE, ref_kinds=["tags"], ref_scan_default="default_branch")

    assert (source.ref_kinds, source.ref_patterns) == (["tags"], [])
    assert source.ref_scan_default == "default_branch"


def test_source_definition_is_frozen_and_normalized() -> None:
    source = SourceDefinition(name="prod", repos=["acme/b", "acme/a", "acme/b"])

    assert source.repos == ["acme/a", "acme/b"]
    with pytest.raises(ValidationError):
        source.name = "other"


@pytest.mark.parametrize(
    ("data", "old", "new", "value"),
    [
        ({"git_fetch_concurrency": 3}, "git_fetch_concurrency", "git_fetch_parallel", 3),
        ({"probe_concurrency": 4}, "probe_concurrency", "probe_parallel", 4),
        ({"stale_after": 60}, "stale_after", "stale_after_seconds", 60),
    ],
)
def test_a_retired_key_is_not_read(
    capsys: pytest.CaptureFixture[str], data: dict[str, object], old: str, new: str, value: object
) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"ansible": data}}}))

    settings = get_config_section("ansible", AnsibleSettings)

    assert getattr(settings, new) == getattr(AnsibleSettings(), new) != value
    assert old not in capsys.readouterr().err


_DELETED = {
    "cache_dir": "deleted in 11.0; the repo store lives under git.store_dir",
    "repo_cache_path": "deleted in 11.0 (via cache_dir); the repo store lives under git.store_dir",
    "git_fetch_depth": "deleted in 11.0; the repo store fetches full history",
    "git_blob_filter": "deleted in 11.0; the repo store is blobless",
    "git_clone_protocol": "deleted in 11.0; set github.git_protocol: ssh to keep ssh",
}


def test_the_git_cache_settings_are_deleted() -> None:
    """The repo store (``git.store_dir``) and ``github.git_protocol`` replaced them."""
    deleted = key_mappings(AnsibleSettings).deleted

    assert {key: entry.reason() for key, entry in deleted.items()} == _DELETED
    assert not set(_DELETED) & set(AnsibleSettings.model_fields)


@pytest.mark.parametrize("key", sorted(_DELETED))
def test_a_deleted_git_cache_setting_is_not_read(key: str) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"profiles": {"default": {"ansible": {key: "ssh"}}}}))

    settings = get_config_section("ansible", AnsibleSettings)

    assert not hasattr(settings, key)

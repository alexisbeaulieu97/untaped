"""Validation rules of the Ansible settings and source models.

Defaults are pinned by the generated ``docs/reference/config.md`` (see
``tests/repo/test_docs.py``), so only the rejection rules live here.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from ansible_test_support import run_root
from untaped.sdk import home, settings_of
from untaped.sdk.health.deprecated_keys import key_mappings, mapping_errors
from untaped.sdk.testing import install
from untaped_ansible.plugin import Ansible
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
    ],
)
def test_a_retired_key_is_not_read(
    capsys: pytest.CaptureFixture[str], data: dict[str, object], old: str, new: str, value: object
) -> None:
    installed = install(Ansible)  # before the config it reads: install writes its sections
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "ansible": data}))

    with installed.running():
        settings = settings_of(AnsibleSettings)

    assert getattr(settings, new) == getattr(AnsibleSettings(), new) != value
    assert old not in capsys.readouterr().err


_DELETED = {
    "cache_dir": "deleted in 11.0; the repo store lives under git.store_dir",
    "repo_cache_path": "deleted in 11.0 (via cache_dir); the repo store lives under git.store_dir",
    "git_fetch_depth": "deleted in 11.0; the repo store fetches full history",
    "git_blob_filter": "deleted in 11.0; the repo store is blobless",
    "git_clone_protocol": "deleted in 11.0; set github.git_protocol: ssh to keep ssh",
    "stale_after": "deleted in 11.0; use ansible.caches.source_data.max_age_seconds",
    "stale_after_seconds": "deleted in 12.0; use ansible.caches.source_data.max_age_seconds",
    "index_path": "deleted in 12.0; ansible.index_dir names the folder that holds the index",
}


def test_the_deleted_settings_say_what_replaced_them() -> None:
    """The repo store (``git.store_dir``), ``github.git_protocol``, the index's folder and its
    source data's cache setting."""
    deleted = key_mappings(AnsibleSettings).deleted

    assert {key: entry.reason() for key, entry in deleted.items()} == _DELETED
    assert not set(_DELETED) & set(AnsibleSettings.model_fields)


def test_the_settings_map_their_old_keys_cleanly() -> None:
    assert mapping_errors(AnsibleSettings) == []


@pytest.mark.parametrize("old", ["stale_after_seconds", "stale_after", "index_path"])
def test_config_migrate_deletes_the_old_index_settings(old: str) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "ansible": {old: 3600}}))

    result = run_root("config", "migrate", "-f", "json")

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["from"], row["action"]) == (f"ansible.{old}", "deleted")
    assert old not in yaml.safe_load(config.read_text())["ansible"]

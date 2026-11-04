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
from untaped.sdk import home
from untaped.sdk.health.deprecated_keys import key_mappings, mapping_errors
from untaped_ansible.settings import AnsibleSettings, SourceDefinition

_SOURCE = {"name": "prod", "repos": ["acme/site"]}


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceDefinition, {**_SOURCE, "ref_scan_default": "main"}),
        (AnsibleSettings, {"ref_scan_default": "main"}),
        (AnsibleSettings, {"git_fetch_parallel": 0}),
        (AnsibleSettings, {"git_fetch_parallel": 33}),
        (AnsibleSettings, {"source_refresh_repo_batch_size": 0}),
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


_DELETED = {
    "stale_after_seconds": "deleted in 12.0; use ansible.caches.source_data.max_age_seconds",
    "index_path": "deleted in 12.0; ansible.index_dir names the folder that holds the index",
    "source_refresh_backend": (
        "deleted in 12.0; each repo's host probes its refs, else git ls-remote does"
    ),
    "probe_parallel": "deleted in 12.0; use git.probe_parallel (live graph reads run 8 at once)",
    "source_refresh_rate_limit_floor": (
        "deleted in 12.0; the plugin that probes a host sets that host's budget floor"
    ),
}


def test_the_deleted_settings_say_what_replaced_them() -> None:
    """The index's folder, its source data's cache setting and the ref probe's settings."""
    deleted = key_mappings(AnsibleSettings).deleted

    assert {key: entry.reason() for key, entry in deleted.items()} == _DELETED
    assert not set(_DELETED) & set(AnsibleSettings.model_fields)


def test_the_settings_map_their_old_keys_cleanly() -> None:
    assert mapping_errors(AnsibleSettings) == []


@pytest.mark.parametrize("old", ["stale_after_seconds", "index_path"])
def test_config_migrate_deletes_the_old_index_settings(old: str) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(yaml.safe_dump({"format_version": 2, "ansible": {old: 3600}}))

    result = run_root("config", "migrate", "-f", "json")

    assert result.exit_code == 0, result.output
    [row] = json.loads(result.stdout)
    assert (row["from"], row["action"]) == (f"ansible.{old}", "deleted")
    assert old not in yaml.safe_load(config.read_text())["ansible"]

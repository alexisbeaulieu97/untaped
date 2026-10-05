"""Validation rules of the Ansible settings and source models.

Defaults are pinned by the generated ``docs/reference/config.md`` (see
``tests/repo/test_docs.py``), so only the rejection rules live here.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from untaped.sdk import get_config_section
from untaped_ansible.settings import AnsibleSettings, SourceDefinition

_SOURCE = {"name": "prod", "repos": ["acme/site"]}


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceDefinition, {**_SOURCE, "ref_scan_default": "main"}),
        (AnsibleSettings, {"ref_scan_default": "main"}),
        (AnsibleSettings, {"git_clone_protocol": "ftp"}),
        (AnsibleSettings, {"git_fetch_depth": -1}),
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
    ("old", "value", "new", "attribute"),
    [
        ("repo_cache_path", "/c", "cache_dir", lambda s: str(s.cache_dir)),
        ("git_fetch_concurrency", 3, "git_fetch_parallel", lambda s: s.git_fetch_parallel),
        ("probe_concurrency", 4, "probe_parallel", lambda s: s.probe_parallel),
        ("stale_after", 60, "stale_after_seconds", lambda s: s.stale_after_seconds),
    ],
)
def test_an_old_key_is_read_as_the_new_one(
    capsys: pytest.CaptureFixture[str],
    old: str,
    value: object,
    new: str,
    attribute: Callable[[AnsibleSettings], object],
) -> None:
    config = Path(os.environ["UNTAPED_CONFIG"])
    config.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    node = data
    *parents, leaf = old.split(".")
    for part in parents:
        node = node.setdefault(part, {})  # type: ignore[assignment]
    node[leaf] = value
    config.write_text(yaml.safe_dump({"profiles": {"default": {"ansible": data}}}))

    assert attribute(get_config_section("ansible", AnsibleSettings)) == value
    assert (
        f"warning: ansible.{old} is deprecated and will be removed in the next major release; "
        f"use ansible.{new}"
    ) in capsys.readouterr().err

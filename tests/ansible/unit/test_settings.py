"""Validation rules of the Ansible settings and source models.

Defaults are pinned by the generated ``docs/reference/config.md`` (see
``tests/unit/test_docs.py``), so only the rejection rules live here.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from untaped.capabilities.ansible.settings import AnsibleSettings, SourceDefinition

_SOURCE = {"name": "prod", "repos": ["acme/site"]}


@pytest.mark.parametrize(
    ("model", "values"),
    [
        (SourceDefinition, {**_SOURCE, "ref_scan_default": "main"}),
        (AnsibleSettings, {"ref_scan_default": "main"}),
        (AnsibleSettings, {"freshness_ttl": -1}),
        (AnsibleSettings, {"git_clone_protocol": "ftp"}),
        (AnsibleSettings, {"git_fetch_depth": -1}),
        (AnsibleSettings, {"git_fetch_concurrency": 0}),
        (AnsibleSettings, {"git_fetch_concurrency": 33}),
        (AnsibleSettings, {"probe_concurrency": 0}),
        (AnsibleSettings, {"probe_concurrency": 33}),
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

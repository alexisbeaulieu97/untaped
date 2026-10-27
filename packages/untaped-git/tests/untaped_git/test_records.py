"""Each record a git command prints takes its kind from its class name."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from untaped.sdk.testing import install
from untaped_git.plugin import Git
from untaped_git.records import Host, StoreReport


@pytest.mark.parametrize(("model", "kind"), [(Host, "git.host"), (StoreReport, "git.store_report")])
def test_a_printed_record_has_the_kind_of_its_class_name(model: type[BaseModel], kind: str) -> None:
    ran = install(Git).cli(["plugin", "schema", kind])
    assert ran.exit == 0, ran.stderr
    schema = json.loads(ran.stdout)
    assert schema["$id"] == kind
    assert set(schema["properties"]) == set(model.model_fields)

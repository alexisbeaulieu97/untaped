"""Unit tests for save CLI runner behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from untaped.sdk import ConfigError, Diagnostics
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.save_runner import save_batch
from untaped_awx.domain import ResourceSpec, ServerRecord
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.resources.specs import JOB_TEMPLATE_SPEC, PROJECT_SPEC


class _Catalog:
    def __init__(self, specs: list[ResourceSpec]) -> None:
        self._by_kind = {spec.kind: spec for spec in specs}

    def get(self, kind: str) -> ResourceSpec:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise ConfigError(f"unknown kind {kind!r}") from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._by_kind)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        raise ConfigError(f"unknown CLI name {cli_name!r}")


class _Repo:
    def list(
        self,
        spec: ResourceSpec,
        *,
        params: dict[str, str] | None = None,
        limit: int | None = None,
    ) -> Any:
        if spec.kind == "Project":
            yield {
                "id": 10,
                "name": "playbooks",
                "organization": 1,
                "scm_type": "git",
            }
            return
        raise AwxApiError("later kind failed")

    def get(self, spec: ResourceSpec, id_: int) -> ServerRecord:
        if spec.kind == "Organization" and id_ == 1:
            return ServerRecord(id=1, name="Default")
        raise AwxApiError(f"unexpected lookup {spec.kind}:{id_}")


class _Fk:
    def id_to_name(self, kind: str, id_: int) -> str:
        if kind == "Organization" and id_ == 1:
            return "Default"
        raise AwxApiError(f"unexpected lookup {kind}:{id_}")


def _ctx(specs: list[ResourceSpec]) -> AwxContext:
    return cast(
        AwxContext,
        SimpleNamespace(
            repo=_Repo(),
            fk=_Fk(),
            catalog=_Catalog(specs),
            workflow_nodes=None,
            diag=Diagnostics(),
        ),
    )


def test_save_batch_writes_previous_records_before_later_failure(tmp_path: Any) -> None:
    out_dir = tmp_path / "backup"
    written = []

    with pytest.raises(AwxApiError, match="later kind failed"):
        for row in save_batch(
            _ctx([PROJECT_SPEC, JOB_TEMPLATE_SPEC]),
            out_dir=out_dir,
            all_kinds=True,
            kind=None,
            filters={},
            organization=None,
        ):
            written.append(row._path)

    expected = out_dir / "Project__Default__playbooks.yml"
    assert expected.exists()
    assert written == [expected]


def test_save_batch_validates_kind_before_creating_out_dir(tmp_path: Any) -> None:
    out_dir = tmp_path / "backup"

    with pytest.raises(ConfigError, match="unknown kind 'Bogus'"):
        list(
            save_batch(
                _ctx([PROJECT_SPEC]),
                out_dir=out_dir,
                all_kinds=False,
                kind="Bogus",
                filters={},
                organization=None,
            )
        )

    assert not out_dir.exists()

"""Unit tests for save CLI runner behavior."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from untaped.capabilities.awx.cli._context import AwxContext
from untaped.capabilities.awx.cli._save_runner import run_save_batch
from untaped.capabilities.awx.domain import ResourceSpec, ServerRecord
from untaped.capabilities.awx.errors import AwxApiError
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC, PROJECT_SPEC


class _Catalog:
    def __init__(self, specs: list[ResourceSpec]) -> None:
        self._by_kind = {spec.kind: spec for spec in specs}

    def get(self, kind: str) -> ResourceSpec:
        try:
            return self._by_kind[kind]
        except KeyError as exc:
            raise AwxApiError(f"unknown kind {kind!r}") from exc

    def kinds(self) -> tuple[str, ...]:
        return tuple(self._by_kind)

    def by_cli_name(self, cli_name: str) -> ResourceSpec:
        raise AwxApiError(f"unknown CLI name {cli_name!r}")


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
        SimpleNamespace(repo=_Repo(), fk=_Fk(), catalog=_Catalog(specs)),
    )


def test_run_save_batch_writes_previous_records_before_later_failure(
    tmp_path: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "backup"

    with pytest.raises(AwxApiError, match="later kind failed"):
        run_save_batch(
            _ctx([PROJECT_SPEC, JOB_TEMPLATE_SPEC]),
            out_dir=out_dir,
            all_kinds=True,
            kind=None,
            filters={},
            organization=None,
            print_paths=True,
        )

    expected = out_dir / "Project__Default__playbooks.yml"
    assert expected.exists()
    assert capsys.readouterr().out.strip() == str(expected)


def test_run_save_batch_validates_kind_before_creating_out_dir(tmp_path: Any) -> None:
    out_dir = tmp_path / "backup"

    with pytest.raises(AwxApiError, match="unknown kind 'Bogus'"):
        run_save_batch(
            _ctx([PROJECT_SPEC]),
            out_dir=out_dir,
            all_kinds=False,
            kind="Bogus",
            filters={},
            organization=None,
            print_paths=False,
        )

    assert not out_dir.exists()

"""A snapshot of the key order each recipe record writes on the wire.

``-f json``, ``-f yaml`` and ``-f pipe`` write a record's keys in this
order, so changing it changes what scripts read. Where a ``list`` row and a
``get`` detail share a kind, one model is both, and each form's sample
lists the same keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from untaped.sdk import ConfigError, Failure, kind_of
from untaped_recipe.records import (
    AddOutcome,
    ApplyOutcome,
    Backup,
    GoldenCase,
    Hook,
    HookRun,
    InstalledRecipe,
    LibraryCheck,
    Pack,
    PackHook,
    PackRecipe,
    PruneOutcome,
    RecipeInput,
    RecipeStep,
    RemoveOutcome,
    RestoreOutcome,
    SyncOutcome,
)

_FAILURE = Failure.from_exception(ConfigError("boom"))
_PACK_OUTCOME: dict[str, str | None] = {
    "name": "acme",
    "source": "/src/acme",
    "rev": None,
    "commit": None,
}
_PACK_KEYS = [
    "name",
    "project",
    "version",
    "path",
    "source",
    "rev",
    "commit",
    "recipes",
    "hooks",
    "manifest_name",
]
_RECIPE_KEYS = ["pack", "name", "ref", "description", "inputs", "steps", "hooks", "path"]
_HOOK_RUN_KEYS = ["hook", "kind", "target", "file", "status", "content", "diff", "message"]

#: (sample, the keys it writes, in wire order). Fields a form does not fill
#: are written as null, so they are listed too.
_CASES: list[tuple[BaseModel, list[str]]] = [
    (
        ApplyOutcome(
            target_path=Path("/w/acme"),
            action="failed",
            files_changed=1,
            warnings=["w"],
            detail="d",
            inputs={"owner": "acme"},
            recipe="acme/ci",
            error=_FAILURE,
        ),
        [
            "target_path",
            "action",
            "files_changed",
            "warnings",
            "detail",
            "inputs",
            "recipe",
            "error",
        ],
    ),
    (
        AddOutcome.model_validate({"action": "created", **_PACK_OUTCOME}),
        ["name", "action", "source", "rev", "commit", "detail"],
    ),
    (
        SyncOutcome.model_validate(
            {"action": "failed", "detail": "d", "error": _FAILURE, **_PACK_OUTCOME}
        ),
        ["name", "action", "source", "rev", "commit", "detail", "error"],
    ),
    (
        RemoveOutcome.model_validate({"action": "removed", **_PACK_OUTCOME}),
        ["name", "action", "source", "rev", "commit", "detail"],
    ),
    (
        PruneOutcome(id="b1", size_bytes=3, action="deleted"),
        ["id", "action", "size_bytes", "detail"],
    ),
    (
        RestoreOutcome(id="b1", files=2, action="failed", detail="d", error=_FAILURE),
        ["id", "action", "files", "detail", "error"],
    ),
    (
        Backup(id="b1", created_at=None, recipe="demo", path="/l/backups/b1"),
        ["id", "created_at", "recipe", "path", "inputs", "files"],
    ),
    (
        InstalledRecipe(pack="acme", name="ci", ref="acme/ci", path="/l/acme/ci.yml"),
        _RECIPE_KEYS,
    ),
    (
        InstalledRecipe(
            pack="acme",
            name="ci",
            ref="acme/ci",
            description="d",
            inputs=[
                RecipeInput(
                    name="owner",
                    type="str",
                    required=False,
                    default="acme",
                    description="",
                    sensitive=False,
                )
            ],
            steps=[RecipeStep(type="validate", files=[], globs=[], exclude=[], hook="h")],
            hooks=["h"],
            path="/l/acme/ci.yml",
        ),
        _RECIPE_KEYS,
    ),
    (
        Hook(pack="acme", name="h", ref="acme/h", module="acme.h", path="/l/acme/h.py"),
        ["pack", "name", "ref", "module", "exports", "path"],
    ),
    (
        Pack(
            name="acme",
            project="acme-pack",
            version="1.0",
            path="/l/acme",
            source="/src/acme",
            rev="",
            commit="",
            recipes=1,
            hooks=0,
        ),
        _PACK_KEYS,
    ),
    (
        Pack(
            name="acme",
            project="acme-pack",
            version="1.0",
            path="/l/acme",
            source="/src/acme",
            rev="",
            commit="",
            recipes=[PackRecipe(name="ci", description="d")],
            hooks=[PackHook(name="h", exports=["validate"])],
            manifest_name="upstream",
        ),
        _PACK_KEYS,
    ),
    (
        HookRun(hook="h", kind="transform", target="/w", file="a.yml", status="ok", content="x\n"),
        _HOOK_RUN_KEYS,
    ),
    (
        HookRun(hook="h", kind="validate", target="/w", status="fail", message="no"),
        _HOOK_RUN_KEYS,
    ),
    (
        LibraryCheck(name="acme", type="pack", status="pass", path="/l/acme"),
        ["name", "type", "status", "path", "detail"],
    ),
    (
        GoldenCase(
            pack="acme", recipe="ci", case="basic", status="error", detail="d", error=_FAILURE
        ),
        ["pack", "recipe", "case", "status", "detail", "error"],
    ),
]


@pytest.mark.parametrize(("sample", "keys"), _CASES)
def test_a_recipe_model_writes_its_old_row_keys_in_their_order(
    sample: BaseModel, keys: list[str]
) -> None:
    assert list(json.loads(sample.model_dump_json())) == keys


def test_every_recipe_kind_has_a_sample() -> None:
    assert len({kind_of(type(sample)) for sample, _keys in _CASES}) == 13

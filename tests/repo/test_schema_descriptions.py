"""A field that documents itself in a schema users read says so with ``Field(description=...)``.

``untaped plugin schema KIND`` prints a record kind's JSON Schema, and
``untaped awx schema`` the schemas of the documents awx reads; the
pages of ``untaped docs`` point at both. A field's description reaches them only through
``Field(description=...)``: pydantic drops the attribute docstring written
after a field unless the model sets ``use_attribute_docstrings``, which no
untaped model does. Docstrings are for whoever reads the Python (``None``,
double backticks); a description is for whoever reads the JSON (``null``,
Markdown).

``silent-field``: a field of a model these schemas reach (every model a
first-party or example kind nests, and the documents ``awx schema`` prints) that has an
attribute docstring and no description. The fix is the description; a
docstring may stay beside it for what only a maintainer needs.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import itertools
import os
import pkgutil
import subprocess
import sys
import textwrap
from collections.abc import Iterable
from typing import get_args

from pydantic import BaseModel, Field

from repo.support import FIRST_PARTY, REPO_ROOT
from untaped.sdk.records import Record, record_kinds
from untaped_awx.cli.commands import DOCUMENT_MODELS


def attribute_docstrings(model: type[BaseModel]) -> list[str]:
    """The fields ``model``'s own class body follows with a string."""
    try:
        source = textwrap.dedent(inspect.getsource(model))
    except OSError, TypeError:  # no source file: nothing to read
        return []
    tree = ast.parse(source).body[0]
    assert isinstance(tree, ast.ClassDef)
    body = tree.body
    return [
        statement.target.id
        for statement, after in itertools.pairwise(body)
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
        and isinstance(after, ast.Expr)
        and isinstance(after.value, ast.Constant)
        and isinstance(after.value.value, str)
    ]


def _models_in(annotation: object) -> list[type[BaseModel]]:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _models_in(arg)]


def silent_fields(roots: Iterable[type[BaseModel]]) -> list[str]:
    """``module.Model.field [silent-field]`` for each field ``roots`` reach with no description."""
    pending = list(roots)
    seen: set[type[BaseModel]] = set()
    found: list[str] = []
    while pending:
        model = pending.pop()
        if model in seen:
            continue
        seen.add(model)
        bases = model.__mro__[1:]
        pending.extend(
            base for base in bases if issubclass(base, BaseModel) and base is not BaseModel
        )
        fields = model.model_fields
        found.extend(
            f"{model.__module__}.{model.__qualname__}.{name} [silent-field]"
            for name in attribute_docstrings(model)
            if name in fields and fields[name].description is None
        )
        for field in fields.values():
            pending.extend(_models_in(field.annotation))
    return sorted(found)


def every_silent_field() -> list[str]:
    """The silent fields once core, the first-party plugins and the examples are imported."""
    sys.path[:0] = [str(path) for path in sorted(REPO_ROOT.glob("examples/*/src"))]
    tops = ["untaped", *(f"untaped_{name}" for name in FIRST_PARTY)]
    tops += [path.name for path in sorted(REPO_ROOT.glob("examples/*/src/*")) if path.is_dir()]
    for top in tops:
        package = importlib.import_module(top)
        for info in pkgutil.walk_packages(package.__path__, f"{top}."):
            if not info.name.endswith(".__main__"):
                importlib.import_module(info.name)
    return silent_fields([*record_kinds().values(), *DOCUMENT_MODELS.values()])


_TESTS = REPO_ROOT / "tests"


def test_every_field_a_schema_shows_has_its_description() -> None:
    # A clean interpreter: the examples' kinds collide with test models this one may hold.
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            f"from {__name__} import every_silent_field as f; print(*f(), sep=chr(10))",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join([str(_TESTS), str(_TESTS.parent / "scripts")]),
        },
    )
    assert proc.returncode == 0, proc.stderr

    assert not proc.stdout.strip(), (
        "give each field Field(description=...), which plugin schema and awx schema show:\n"
        + proc.stdout
    )


class _Nested(BaseModel):
    note: str = ""
    """Silent: nested in a record."""


class _Base(Record):
    kept: int = Field(default=0, description="For the JSON reader.")
    """For the maintainer."""


class _Widget(_Base):
    count: int
    """Silent."""
    nested: tuple[_Nested, ...] = ()
    plain: str = ""


def test_silent_field_names_each_field_its_models_reach() -> None:
    assert silent_fields([_Widget]) == [
        f"{__name__}._Nested.note [silent-field]",
        f"{__name__}._Widget.count [silent-field]",
    ]

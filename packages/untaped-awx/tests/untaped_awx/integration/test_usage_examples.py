"""Run the guides' inventory examples through scope policy and the HTTP adapter.

The guides are the docstrings of awx's bundles and documents, so the
examples are read from its source.
"""

import shlex
from pathlib import Path
from typing import Any

import pytest

from awx_test_support import run
from untaped.sdk.testing import ScriptedTerminal

PACKAGE = Path(__file__).resolve().parents[3]
GUIDES = sorted((PACKAGE / "src/untaped_awx").rglob("*.py"))
EXAMPLES = [
    shlex.split(line)[2:]
    for guide in GUIDES
    for line in (
        raw.strip().removeprefix("$ ")
        for raw in guide.read_text().replace("\\\\\n", " ").splitlines()
    )
    if line.startswith("untaped awx inventory")
    and "--inventory" in line
    and shlex.split(line)[3] in {"list", "export", "get", "patch"}
]


def test_the_guide_has_inventory_examples() -> None:
    assert EXAMPLES, "no 'untaped awx inventory … --inventory' examples found"


@pytest.mark.parametrize("args", EXAMPLES)
def test_inventory_scope_guide_examples_execute(
    fake_aap: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    fake_aap.seed("organizations", id=1, name="Default")
    for id_, inventory, source in [
        (10, "Production", "Cloud"),
        (11, "Disposable", "DisposableSource"),
    ]:
        fake_aap.seed("inventories", id=id_, name=inventory, organization=1, kind="")
        fake_aap.seed(
            "inventory_sources",
            id=id_ + 10,
            name=source,
            inventory=id_,
            source="scm",
            update_cache_timeout=60,
            update_on_launch=False,
        )
    result = run(args, interactive=True, prompt_backend=ScriptedTerminal().answers(True))
    assert result.exit_code == 0, result.output + result.stderr
    assert fake_aap.router.calls
    if args[1] == "export":
        output = Path(args[args.index("--out") + 1]).read_text()
        assert "kind: InventorySource" in output
        assert "organization: Default" in output
    if args[1] == "patch" and "--dry-run" not in args:
        id_ = 20 if "Production" in args else 21
        assert fake_aap.get_record("inventory_sources", id_)["update_cache_timeout"] == (
            3600 if id_ == 20 else 0
        )
        assert fake_aap.get_record("inventory_sources", id_)["update_on_launch"] is False

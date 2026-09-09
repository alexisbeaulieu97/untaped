"""Execute inventory guide examples through runtime scope policy and the HTTP adapter."""

import shlex
from pathlib import Path
from typing import Any

import pytest

from untaped.capabilities.awx.cli.commands import app
from untaped.testing import CliInvoker, ScriptedPromptBackend

GUIDE = Path(__file__).resolve().parents[3] / "docs/awx/usage.md"
EXAMPLES = [
    shlex.split(line)[2:]
    for line in GUIDE.read_text().replace("\\\n", " ").splitlines()
    if line.startswith("untaped awx inventory")
    and "--inventory" in line
    and shlex.split(line)[3] in {"list", "save", "get", "patch"}
]


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
    result = CliInvoker().invoke(
        app, args, interactive=True, prompt_backend=ScriptedPromptBackend(confirms=[True])
    )
    assert result.exit_code == 0, result.output + result.stderr
    assert fake_aap.router.calls
    if args[1] == "save":
        output = Path(args[args.index("--out") + 1]).read_text()
        assert "kind: InventorySource" in output
        assert "organization: Default" in output
    if args[1] == "patch" and "--dry-run" not in args:
        id_ = 20 if "Production" in args else 21
        assert fake_aap.get_record("inventory_sources", id_)["update_cache_timeout"] == (
            3600 if id_ == 20 else 0
        )
        assert fake_aap.get_record("inventory_sources", id_)["update_on_launch"] is False

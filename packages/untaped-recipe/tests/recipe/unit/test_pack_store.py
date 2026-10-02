"""Tests for the unified pack library store."""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from untaped.sdk import UsageError
from untaped_recipe.domain.pack import InstalledPack, PackManifest, parse_ref
from untaped_recipe.errors import (
    AmbiguousRefError,
    HookNotFoundError,
    RecipeError,
    RecipeNotFoundError,
)
from untaped_recipe.infrastructure import pack_store
from untaped_recipe.infrastructure.pack_store import (
    PackLibrary,
    changed_hook_files,
    checkout_commit,
    fetch_pack_source,
    is_git_url,
    pack_content_hash,
)


def _write_pack(
    root: Path,
    *,
    manifest_name: str,
    version: str = "0.1.0",
    recipes: dict[str, str] | None = None,
    hooks: dict[str, str] | None = None,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    recipe_rows: list[str] = []
    for name, relative in (recipes or {}).items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("version: 1\nsteps: []\n", encoding="utf-8")
        recipe_rows.append(f'"{name}" = {{ path = "{relative}" }}')
    hook_rows: list[str] = []
    for name, module in (hooks or {}).items():
        module_path = root / "src" / Path(*module.split(".")).with_suffix(".py")
        module_path.parent.mkdir(parents=True, exist_ok=True)
        module_path.write_text(
            "def transform(content, *, inputs, target, file, args, helpers):\n    return content\n",
            encoding="utf-8",
        )
        for parent in [root / "src" / Path(*module.split(".")[:1])]:
            (parent / "__init__.py").write_text("", encoding="utf-8")
        if len(module.split(".")) > 2:
            package = root / "src" / Path(*module.split(".")[:-1])
            (package / "__init__.py").write_text("", encoding="utf-8")
        hook_rows.append(f'"{name}" = {{ module = "{module}" }}')
    (root / "pyproject.toml").write_text(
        "[project]\n"
        f'name = "untaped-recipe-{manifest_name}"\n'
        f'version = "{version}"\n'
        'requires-python = ">=3.14"\n'
        "dependencies = []\n\n"
        "[tool.untaped_recipe]\n"
        'requires_hook_api = ">=0.8,<1"\n'
        + (
            "\n[tool.untaped_recipe.recipes]\n" + "\n".join(recipe_rows) + "\n"
            if recipe_rows
            else ""
        )
        + ("\n[tool.untaped_recipe.hooks]\n" + "\n".join(hook_rows) + "\n" if hook_rows else ""),
        encoding="utf-8",
    )
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")


def test_pack_library_adds_and_finds_recipes_and_hooks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"set_owner": "ansible_hooks.hooks.set_owner"},
    )
    library = PackLibrary(library_root=tmp_path / "library")

    manifest = library.add(source, source=str(source), rev=None, name=None, force=False)

    assert manifest.name == "ansible"
    recipe_pack, recipe = library.find_recipe(parse_ref("playbook"))
    assert isinstance(recipe_pack, InstalledPack)
    assert recipe_pack.name == "ansible"
    assert recipe.path == "recipes/playbook/recipe.yml"
    hook_pack, hook = library.find_hook(parse_ref("ansible/set_owner"))
    assert hook_pack.name == "ansible"
    assert hook.module == "ansible_hooks.hooks.set_owner"
    assert not (library.packs_dir / "ansible" / ".git").exists()


def test_pack_library_ambiguity_lists_installed_candidates(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a"
    source_b = tmp_path / "source-b"
    _write_pack(source_a, manifest_name="manifest-a", hooks={"x": "a_hooks.hooks.x"})
    _write_pack(source_b, manifest_name="manifest-b", hooks={"x": "b_hooks.hooks.x"})
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source_a, source=str(source_a), rev=None, name="a", force=False)
    library.add(source_b, source=str(source_b), rev=None, name="b", force=False)

    with pytest.raises(ValueError) as excinfo:
        library.find_hook(parse_ref("x"))

    message = str(excinfo.value)
    assert "a/x" in message
    assert "b/x" in message
    assert "manifest-a/x" not in message
    assert "manifest-b/x" not in message


def test_pack_library_duplicate_requires_force_or_name(tmp_path: Path) -> None:
    source = tmp_path / "source"
    replacement = tmp_path / "replacement"
    _write_pack(source, manifest_name="ansible", version="0.1.0")
    _write_pack(replacement, manifest_name="ansible", version="0.2.0")
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source=str(source), rev=None, name=None, force=False)

    with pytest.raises(RecipeError, match=r"ansible.*--force.*--name") as raised:
        library.add(replacement, source=str(replacement), rev=None, name=None, force=False)
    assert raised.value.category == "conflict"

    library.add(replacement, source=str(replacement), rev=None, name=None, force=True)

    assert library.packs()[0].installed_version == "0.2.0"


def test_pack_library_name_override_is_installed_identity(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(
        source,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
        hooks={"set_owner": "ansible_hooks.hooks.set_owner"},
    )
    library = PackLibrary(library_root=tmp_path / "library")

    library.add(source, source=str(source), rev=None, name="alias", force=False)

    installed = library.packs()[0]
    assert installed.name == "alias"
    assert installed.manifest.name == "ansible"
    assert library.find_recipe(parse_ref("alias/playbook"))[0].name == "alias"
    with pytest.raises(RecipeNotFoundError, match="recipe not found: 'ansible/playbook'"):
        library.find_recipe(parse_ref("ansible/playbook"))


@pytest.mark.parametrize("ref", ["missing", "ansible/missing"])
def test_pack_library_misses_raise_typed_not_found_errors(tmp_path: Path, ref: str) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"playbook": "recipes/playbook.yml"})
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source=str(source), rev=None, name=None, force=False)

    with pytest.raises(RecipeNotFoundError, match=f"recipe not found: '{ref}'"):
        library.find_recipe(parse_ref(ref))
    with pytest.raises(HookNotFoundError, match=f"hook not found: '{ref}'"):
        library.find_hook(parse_ref(ref))


def test_pack_library_ambiguous_bare_refs_raise_typed_errors(tmp_path: Path) -> None:
    library = PackLibrary(library_root=tmp_path / "library")
    for name in ("one", "two"):
        source = tmp_path / name
        _write_pack(
            source,
            manifest_name=name,
            recipes={"shared": "recipes/shared.yml"},
            hooks={"shared": f"{name}_hooks.hooks.shared"},
        )
        library.add(source, source=str(source), rev=None, name=None, force=False)

    with pytest.raises(AmbiguousRefError, match="ambiguous recipe ref 'shared'"):
        library.find_recipe(parse_ref("shared"))
    with pytest.raises(AmbiguousRefError, match="ambiguous hook ref 'shared'"):
        library.find_hook(parse_ref("shared"))


def test_pack_library_remove_deletes_pack_and_index_row(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible")
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source=str(source), rev="main", name=None, force=False)

    library.remove("ansible")

    assert not (library.packs_dir / "ansible").exists()
    index = tomllib.loads(library.index_path.read_text(encoding="utf-8"))
    assert "ansible" not in index


def test_pack_library_index_round_trips_source_rev_and_version(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", version="0.3.0")
    library = PackLibrary(library_root=tmp_path / "library")

    library.add(
        source,
        source="git+https://example.test/pack.git",
        rev="main",
        commit="0123456789abcdef0123456789abcdef01234567",
        name=None,
        force=False,
    )

    installed = library.packs()[0]
    assert installed.source == "git+https://example.test/pack.git"
    assert installed.rev == "main"
    assert installed.commit == "0123456789abcdef0123456789abcdef01234567"
    assert installed.installed_version == "0.3.0"
    index = tomllib.loads(library.index_path.read_text(encoding="utf-8"))
    content_hash = index["ansible"].pop("content_hash")
    assert content_hash == pack_content_hash(library.packs_dir / "ansible")
    assert index["ansible"] == {
        "source": "git+https://example.test/pack.git",
        "rev": "main",
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "version": "0.3.0",
    }


def test_changed_hook_files_lists_hook_code_that_differs(tmp_path: Path) -> None:
    installed = tmp_path / "installed"
    fetched = tmp_path / "fetched"
    hooks = {"a": "pack_hooks.hooks.a", "b": "pack_hooks.hooks.b"}
    for root in (installed, fetched):
        _write_pack(root, manifest_name="ansible", recipes={"r": "recipes/r.yml"}, hooks=hooks)
    (fetched / "src" / "pack_hooks" / "hooks" / "a.py").write_text("def transform(): ...\n")
    (fetched / "src" / "pack_hooks" / "hooks" / "b.py").unlink()
    (fetched / "src" / "pack_hooks" / "extra.py").write_text("X = 1\n")
    (fetched / "uv.lock").write_text("version = 2\n")
    (fetched / "recipes" / "r.yml").write_text("version: 1\ndescription: x\nsteps: []\n")
    (fetched / "__pycache__").mkdir()
    (fetched / "__pycache__" / "junk.pyc").write_text("")
    for build_file in ("setup.py", "hatch_build.py", ".python-version", "uv.toml", "setup.cfg"):
        (fetched / build_file).write_text("changed\n")
    (fetched / "recipes" / "helper.py").write_text("# template, not executed\n")

    assert changed_hook_files(installed, fetched) == [
        ".python-version",
        "hatch_build.py",
        "setup.cfg",
        "setup.py",
        "src/pack_hooks/extra.py",
        "src/pack_hooks/hooks/a.py",
        "src/pack_hooks/hooks/b.py",
        "uv.lock",
        "uv.toml",
    ]


def test_pack_library_rejects_a_symlink_that_appears_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible", recipes={"r": "recipes/r.yml"})
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n")
    real_validate = pack_store.validate_pack

    def validate_then_swap(source_dir: Path, manifest: PackManifest) -> None:
        real_validate(source_dir, manifest)
        (source_dir / "recipes" / "leak.txt").symlink_to(secret)

    monkeypatch.setattr(pack_store, "validate_pack", validate_then_swap)
    library = PackLibrary(library_root=tmp_path / "library")

    with pytest.raises(ValueError, match=r"symlinks: recipes/leak\.txt"):
        library.add(source, source=str(source), rev=None, name=None, force=False)

    assert not (library.packs_dir / "ansible").exists()
    assert [path.name for path in (tmp_path / "library").iterdir()] == ["packs"]


def test_pack_library_record_commit_fills_a_legacy_row_without_commit(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_pack(source, manifest_name="ansible")
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source="https://example.test/p.git", rev="main", name=None, force=False)
    assert "commit" not in library.index_path.read_text(encoding="utf-8")
    assert library.packs()[0].commit == ""

    library.record_commit("ansible", "3" * 40)

    assert PackLibrary(library_root=tmp_path / "library").packs()[0].commit == "3" * 40
    with pytest.raises(ValueError, match="pack not found: ghost"):
        library.record_commit("ghost", "3" * 40)


@pytest.mark.parametrize(
    ("index_text", "message"),
    [
        ('"broken" = "not-a-table"\n', "must be a table"),
        ('[broken]\nsource = "local"\n', "requires content_hash"),
        ('[broken]\ncontent_hash = ""\n', "requires content_hash"),
        ("[broken]\ncontent_hash = 42\n", "requires content_hash"),
    ],
)
def test_pack_library_rejects_incomplete_index_rows(
    tmp_path: Path, index_text: str, message: str
) -> None:
    library_root = tmp_path / "library"
    library_root.mkdir()
    (library_root / "packs.toml").write_text(index_text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        PackLibrary(library_root=library_root).reconcile()


def test_pack_library_reconcile_reports_stale_index_and_orphan_directory(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    stale_source = tmp_path / "stale-source"
    _write_pack(source, manifest_name="ansible")
    _write_pack(stale_source, manifest_name="stale")
    library = PackLibrary(library_root=tmp_path / "library")
    library.add(source, source=str(source), rev=None, name="recorded", force=False)
    library.add(stale_source, source=str(stale_source), rev=None, name="stale", force=False)
    shutil.rmtree(library.packs_dir / "stale")
    _write_pack(library.packs_dir / "orphan", manifest_name="orphan")

    assert library.reconcile() == {
        "stale": "pack 'stale' is in packs.toml but missing from packs/",
        "orphan": "pack directory 'orphan' is not recorded in packs.toml",
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://example.test/pack.git", True),
        ("git@example.test:pack.git", True),
        ("ssh://example.test/pack.git", True),
        ("file:///tmp/pack.git", False),
        ("./local-pack", False),
        ("ansible/playbook", False),
    ],
)
def test_is_git_url_matches_supported_cli_prefixes(value: str, expected: bool) -> None:
    assert is_git_url(value) is expected


def test_fetch_pack_source_clones_local_file_url(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write_pack(
        repo,
        manifest_name="ansible",
        recipes={"playbook": "recipes/playbook/recipe.yml"},
    )
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "--no-gpg-sign", "-m", "initial"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )

    checkout = fetch_pack_source(repo.as_uri(), rev=None, dest=tmp_path / "checkout")

    assert checkout == tmp_path / "checkout"
    assert (checkout / "pyproject.toml").is_file()
    assert (checkout / "recipes" / "playbook" / "recipe.yml").is_file()


def _git_pack_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _write_pack(repo, manifest_name="ansible", recipes={"playbook": "recipes/playbook/recipe.yml"})
    shutil.rmtree(repo / ".git")
    git = ["git", "-c", "user.email=test@example.invalid", "-c", "user.name=Test User"]
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [*git, "commit", "--no-gpg-sign", "-m", "first"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo / "later.txt").write_text("later\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        [*git, "commit", "--no-gpg-sign", "-m", "second"],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
    )
    return repo, first


def test_fetch_pack_source_checks_out_commit_rev(tmp_path: Path) -> None:
    repo, first = _git_pack_repo(tmp_path)

    checkout = fetch_pack_source(repo.as_uri(), rev=first, dest=tmp_path / "checkout")

    assert (checkout / "pyproject.toml").is_file()
    assert not (checkout / "later.txt").exists()
    assert checkout_commit(checkout) == first


def test_checkout_commit_resolves_a_branch_checkout_to_its_sha(tmp_path: Path) -> None:
    repo, _ = _git_pack_repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()

    checkout = fetch_pack_source(repo.as_uri(), rev=None, dest=tmp_path / "checkout")

    assert checkout_commit(checkout) == head


def test_checkout_commit_reports_a_non_git_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="rev-parse"):
        checkout_commit(tmp_path)


def test_fetch_pack_source_rejects_option_like_rev(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: calls.append(args))

    with pytest.raises(UsageError, match="rev"):
        fetch_pack_source(
            "https://example.invalid/p.git", rev="--upload-pack=x", dest=tmp_path / "d"
        )
    assert calls == []


def test_fetch_pack_source_does_not_retry_full_clone_on_auth_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(
            args, 128, stdout="", stderr="fatal: Authentication failed for 'https://x/'"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ValueError, match="Authentication failed"):
        fetch_pack_source("https://x/p.git", rev="v1", dest=tmp_path / "d")
    assert len(calls) == 1


def test_pack_library_force_replace_keeps_old_pack_when_copy_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    replacement = tmp_path / "replacement"
    _write_pack(source, manifest_name="ansible", version="0.1.0")
    _write_pack(replacement, manifest_name="ansible", version="0.2.0")
    library_root = tmp_path / "library"
    library = PackLibrary(library_root=library_root)
    library.add(source, source=str(source), rev=None, name=None, force=False)
    index_before = (library_root / "packs.toml").read_text()

    def failing_copytree(src: Path, dst: Path, **kwargs: object) -> Path:
        Path(dst).mkdir(parents=True)
        (Path(dst) / "partial.txt").write_text("partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", failing_copytree)

    with pytest.raises(OSError, match="disk full"):
        library.add(replacement, source=str(replacement), rev=None, name=None, force=True)

    fresh = PackLibrary(library_root=library_root)
    assert fresh.packs()[0].installed_version == "0.1.0"
    assert (library_root / "packs.toml").read_text() == index_before
    assert sorted(path.name for path in library_root.iterdir()) == ["packs", "packs.toml"]
    assert [path.name for path in (library_root / "packs").iterdir()] == ["ansible"]


def test_fetch_pack_source_runs_git_non_interactive_in_c_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "fr")
    seen: list[dict[str, object]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    fetch_pack_source("https://x/p.git", rev="v1", dest=tmp_path / "d")

    assert len(seen) == 1
    env = seen[0]["env"]
    assert isinstance(env, dict)
    assert env["LC_ALL"] == "C"
    assert env["LANGUAGE"] == "C"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["GCM_INTERACTIVE"] == "never"
    assert seen[0]["stdin"] is subprocess.DEVNULL


def test_pack_library_force_replace_keeps_retired_pack_when_swap_and_rollback_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    replacement = tmp_path / "replacement"
    _write_pack(source, manifest_name="ansible", version="0.1.0")
    _write_pack(replacement, manifest_name="ansible", version="0.2.0")
    library_root = tmp_path / "library"
    library = PackLibrary(library_root=library_root)
    library.add(source, source=str(source), rev=None, name=None, force=False)
    real_rename = Path.rename

    def flaky_rename(self: Path, target: str | Path) -> Path:
        if self.name.startswith(".pack-"):
            raise OSError(f"cannot rename {self.name}")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", flaky_rename)

    with pytest.raises(OSError) as excinfo:
        library.add(replacement, source=str(replacement), rev=None, name=None, force=True)

    retired = [path for path in library_root.iterdir() if path.name.startswith(".pack-retired-")]
    assert len(retired) == 1
    assert str(retired[0]) in str(excinfo.value)
    manifest = (retired[0] / "pyproject.toml").read_text()
    assert 'version = "0.1.0"' in manifest

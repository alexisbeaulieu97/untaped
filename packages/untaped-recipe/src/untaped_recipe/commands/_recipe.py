"""What every recipe command shares: the library, the hook workers, and how errors read."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field

from untaped.sdk import Command, ErrorCategory, UntapedError, read_structured_file
from untaped_recipe.domain.hook_project import HookKind
from untaped_recipe.domain.pack import InstalledPack
from untaped_recipe.errors import RecipeError
from untaped_recipe.infrastructure import BackupStore, HookExecutor, HookResolver, PackLibrary
from untaped_recipe.infrastructure.hook_worker_client import UvHookWorkerPool
from untaped_recipe.settings import LIBRARY, RecipeSettings

#: How a recipe ref reads in help.
RECIPE_REF = "Recipe name, PACK/RECIPE reference, or path to a recipe file."

#: What ``untaped recipe packs init`` and friends print when the library is empty.
EMPTY_LIBRARY_HINT = (
    "no packs installed; scaffold one with `untaped recipe packs init NAME` "
    "or install one with `untaped recipe packs add PATH|GIT_URL`"
)


class Hooks(BaseModel):
    """Pack hooks run as you: what pack code runs, when, and what it can reach.

    Read before installing, syncing or running a pack you haven't vetted.

    ## Installing a pack installs code

    A pack's hooks are Python that runs as you, with full file access and no
    sandbox: the same trust model as `pip install`. They run whenever a plan is
    computed, so on every `recipe apply`, `--dry-run` and `--check` included,
    and in every golden test (`recipe test`). A dry run is no way to inspect a
    pack you don't trust yet; `packs add`, `packs get`, `get`, `hooks get` and
    `validate` read one without importing any of its hooks.

    Hooks, scaffolding and `validate` run `uv` on the pack, so they need `uv`
    on `PATH`; without it they exit 4. Pack hooks run in the pack's own
    environment through `uv run`; built-in hooks such as `yaml_edit` run in
    untaped's process.

    ## What hooks can reach

    Hook workers, and the `uv` commands run on a pack (`uv run`, `uv lock`,
    `uv lock --check`), get only this environment:

    - `PATH`, `HOME`, `USER`/`LOGNAME`, `TZ` and temp directories;
    - locale: `LANG`, `LANGUAGE`, `LC_*`;
    - `UV_*` (which may hold package-index credentials) and `XDG_*`, `NETRC`;
    - TLS trust: `SSL_CERT_FILE`, `SSL_CERT_DIR`, `REQUESTS_CA_BUNDLE`,
      `CURL_CA_BUNDLE`;
    - proxies: `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`;
    - for `git+ssh` dependencies: `SSH_AUTH_SOCK`, `GIT_SSH_COMMAND`;
    - hook workers only: `PYTHONPATH`, set to the pack's `src/`.

    Tokens such as `GITHUB_TOKEN` and untaped's `UNTAPED_*` credentials are not
    passed. Hooks still run as you, so `~/.netrc`, untaped's `config.yml` and
    git credential stores are readable. Hooks read an empty stdin; anything they
    print becomes diagnostics on stderr.

    ## Time limits

    `--hook-timeout` bounds each hook call (`recipe.hook_timeout_seconds` by
    default). Preparing a pack's hook environment has its own bound,
    `recipe.hook_startup_timeout_seconds`, and prints `preparing hook
    environment...`.
    """

    hook_timeout: float | None = Field(
        None,
        ge=0,
        allow_inf_nan=False,
        description="Per-hook timeout in seconds; 0 disables it.",
    )


class HookContract(StrEnum):
    """The callable a pack hook exports: how to write one, and how its name resolves.

    Read before writing a pack hook or using the built-in `yaml_edit` hook.

    ## The contract

    A hook module exports `transform()`, `validate()`, or both; the exported
    name is the contract, and the pack's manifest rows declare only `module`.
    `untaped recipe hooks init` scaffolds one, and `untaped recipe hooks run`
    runs one against a fixture.

    - A step's `args` reach the hook verbatim. Hooks read the resolved `inputs`
      natively (lists and dicts stay structured) and call
      `helpers.render_template()` for templated string args.
    - Hooks stay pure while planning: they read only the target tree and their
      own pack, and never write or reach the network.
    - A validate hook returns `helpers.pass_()`, `helpers.fail(msg)` or
      `helpers.skip(msg)`; skip means the recipe doesn't apply, and the target
      is `skipped`, not failed. Returning `None` passes; a plain string fails
      with that message.
    - `helpers.warn(msg)` adds a warning to the target, any number of times,
      from validate and transform hooks; the hook still returns its verdict.

    ## How a hook name resolves

    A bare hook name in a pack's recipe is the pack's own hook, else a
    built-in, never another pack's; `pack/hook` names another pack's. Only
    recipes outside a pack look bare names up across installed packs.

    ## The built-in `yaml_edit` hook

    - `edits` is a list of `op: set|merge|delete|ensure`, each with a `path` of
      mapping keys, `{index: N}` or first-match `{where: {...}}` selectors.
    - `ensure` adds a value only when absent: list membership by `match` keys
      or equality, or set-if-absent for mappings.
    - String values render `{{ input }}` tokens and honour the args-level
      `unknown_tokens: keep`.
    - A file whose content wouldn't change stays byte-identical.

    `untaped docs recipe inputs recipe_file` has a recipe that uses it.
    """

    TRANSFORM = "transform"
    VALIDATE = "validate"

    @property
    def kind(self) -> HookKind:
        """The engine's name for this callable."""
        return "validate" if self is HookContract.VALIDATE else "transform"


class RecipeCommand(Command[RecipeSettings]):
    """A command over the recipe library the profile's ``recipe`` settings place."""

    @property
    def root(self) -> Path:
        """The library folder: installed packs, ``packs.toml`` and ``backups/``."""
        return LIBRARY.open()

    def library(self) -> PackLibrary:
        return PackLibrary(library_dir=self.root)

    def installed(self) -> list[InstalledPack]:
        """Every loadable installed pack; each that doesn't load is skipped with a warning."""
        library = self.library()
        installed = library.packs()
        for name, error in library.load_errors().items():
            self.ctx.diag.warning(f"skipping pack '{name}': {error}")
        return installed

    def empty_hint(self) -> None:
        """Say how to get a first pack."""
        self.ctx.diag.info(EMPTY_LIBRARY_HINT)

    def backups(self) -> BackupStore:
        return BackupStore(self.root / "backups")

    def hook_timeout(self, hooks: Hooks | None = None) -> float:
        """The per-hook timeout: ``--hook-timeout``, else the setting."""
        given = None if hooks is None else hooks.hook_timeout
        return self.settings.hook_timeout_seconds if given is None else given

    @contextmanager
    def executor(self, hooks: Hooks | None = None, *, workers: int = 1) -> Iterator[HookExecutor]:
        """Hooks resolved from the library, run by ``workers`` uv workers per pack."""

        def notice(project_root: Path) -> None:
            self.ctx.diag.info(f"preparing hook environment for {project_root}...")

        with UvHookWorkerPool(
            max_workers_per_project=workers,
            hook_timeout_seconds=self.hook_timeout(hooks),
            startup_timeout_seconds=self.settings.hook_startup_timeout_seconds,
            startup_notice=notice,
        ) as pool:
            yield HookExecutor(HookResolver(library_dir=self.root), workers=pool)


@contextmanager
def recipe_errors() -> Iterator[None]:
    """Expected library and input errors as clean ``error:`` lines, never tracebacks.

    An :class:`UntapedError` (typed recipe errors included) keeps its own
    category. Any other ``ValueError`` (the remaining plain validation raises
    of recipe, pack and hook files, a pydantic ``ValidationError``, a YAML
    error) is invalid local input: a :class:`RecipeError` with the same
    message (exit ``1``).
    """
    try:
        yield
    except UntapedError:
        raise
    except ValueError as exc:
        raise RecipeError(str(exc)) from exc


def as_recipe_error[T, R](action: Callable[[T], R]) -> Callable[[T], R]:
    """Wrap ``action`` so its expected library errors are per-item ``UntapedError``s.

    Typed errors keep their category; a plain ``ValueError`` is invalid input
    and an ``OSError`` a failed file operation (both in ``local``).
    """

    def wrapped(item: T) -> R:
        try:
            return action(item)
        except UntapedError:
            raise
        except ValueError as exc:
            raise RecipeError(str(exc)) from exc
        except OSError as exc:
            raise RecipeError(str(exc), category=ErrorCategory.FAILED) from exc

    return wrapped


def merge_vars(
    files: Sequence[Path], values: Mapping[str, object], *, flag: str
) -> dict[str, object]:
    """Merge YAML mapping ``files`` in order (later wins), then ``values`` on top.

    Pairs given on the command line win over every file, and a later file wins
    over an earlier one.
    """
    merged: dict[str, object] = {}
    for path in files:
        merged.update(read_structured_file(path, flag=flag))
    merged.update(values)
    return merged

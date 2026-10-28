"""``untaped recipe hooks ...``: inspect, scaffold and run recipe hooks."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import yaml
from pydantic import Field

from untaped.sdk import (
    ExistingFile,
    Failure,
    Group,
    KeyValue,
    Output,
    Positional,
    TextOrStdin,
    UsageError,
    Verbatim,
    run_editor,
    unified_diff_text,
)
from untaped_recipe.application.run_hook import (
    AmbiguousHookVerbError,
    RunHook,
    TransformHookRun,
    ValidateHookRun,
    select_verb,
)
from untaped_recipe.builtins.registry import BUILTIN_HOOKS
from untaped_recipe.commands._library import NO_LOCK_NOTE, find_hook, pack_child
from untaped_recipe.commands._recipe import (
    HookContract,
    Hooks,
    RecipeCommand,
    merge_vars,
    recipe_errors,
)
from untaped_recipe.commands.detail import hook_record
from untaped_recipe.commands.library import LOCK
from untaped_recipe.domain.hook_project import hook_module_file
from untaped_recipe.domain.paths import is_path_ref
from untaped_recipe.errors import PathNotFoundError, RecipeError
from untaped_recipe.infrastructure import pack_scaffold
from untaped_recipe.infrastructure.hook_resolver import HookResolver
from untaped_recipe.infrastructure.pack_files import read_hook_project
from untaped_recipe.records import Hook, HookRun

#: How a hook ref reads in help.
HOOK_REF = "Hook name, PACK/HOOK reference, or built-in."


class List(RecipeCommand):
    """List installed hooks, then the built-in ones.

    A built-in such as `yaml_edit` is listed with pack `(builtin)`.
    """

    output: Output[Hook]

    def run(self) -> Iterator[Hook]:
        with recipe_errors():
            installed = self.installed()
        if not installed and not BUILTIN_HOOKS:
            self.empty_hint()
        for pack in installed:
            for name in sorted(pack.manifest.hooks):
                yield hook_record(pack, name, detail=False)
        for name in sorted(BUILTIN_HOOKS):
            yield hook_record(None, name, detail=False)


class Get(RecipeCommand):
    """Show an installed or built-in hook, with the callables it exports."""

    ref: Positional[str] = Field(description=HOOK_REF)
    output: Output[Hook]

    def run(self) -> Hook:
        with recipe_errors():
            target = find_hook(self.library(), self.ref)
            return hook_record(target.pack, target.name, detail=True)


class Edit(RecipeCommand):
    """Open an installed hook module in $VISUAL or $EDITOR; built-in hooks can't be edited."""

    ref: Positional[str] = Field(description="Hook name or PACK/HOOK reference.")

    def run(self) -> None:
        with recipe_errors():
            target = find_hook(self.library(), self.ref)
            if target.pack is None or target.hook is None:
                raise RecipeError(
                    f"built-in hooks are engine-owned and cannot be edited: {target.name}"
                )
            run_editor(hook_module_file(target.pack.root, target.hook.module))


class Init(RecipeCommand):
    """Scaffold a hook module and its pytest inside a pack, and print the module's path.

    It writes a typed stub of the --kind callable and a pytest that calls it
    directly. A `./path/HOOK` ref targets the pack at `./path`. An existing
    hook is refused without --force, which replaces both files. It locks the
    pack like `packs init`.
    """

    ref: Positional[str] = Field(description="PACK/HOOK reference (or ./path/HOOK).")
    kind: HookContract = Field(HookContract.TRANSFORM, description="The callable to stub.")
    force: bool = Field(
        False, description="Replace an existing hook's stub and paired test, e.g. wrong --kind."
    )
    lock: bool = Field(True, description=LOCK)

    def run(self) -> str:
        kind = self.kind.kind
        with recipe_errors():
            pack_dir, name = pack_child(self.library(), self.ref)
            path = pack_scaffold.scaffold_hook(
                pack_dir, name, kind=kind, lock=self.lock, force=self.force
            )
        if not self.lock:
            self.ctx.diag.warning(NO_LOCK_NOTE.format(path=pack_dir))
        self.ctx.diag.info(
            f"scaffolded {kind} hook (choose with --kind transform|validate; "
            "replace an existing hook with --force)",
        )
        return str(path)


def _text(row: HookRun) -> str | None:
    """The ``text`` format: a transform's content (or diff), a validate's verdict."""
    if row.kind == "transform":
        return Verbatim(row.diff if row.diff is not None else row.content or "")
    return f"{row.status}: {row.message}" if row.message else row.status


class Run(RecipeCommand):
    """Run one hook once against explicit fixture context without writing files.

    A transform needs --file. In the default `text` format it prints the
    transformed content, or a diff with --diff; a validate prints its verdict
    (`pass`, `fail: MESSAGE` or `skip: MESSAGE`) and exits 1 only on `fail`.
    Other formats print the `recipe.hook_run` record. A hook that raises
    prints its traceback as an `error:` and exits 1. --var and --arg values
    are parsed as YAML, so quote strings such as `'v="3.10"'`.

    A local hook project runs only when named, with --project or a
    `./pack/hook` ref; running inside a cloned repo never runs its hooks.
    Without --project a bare NAME is looked up across installed packs, then
    the built-ins. The context (fixture values included) and the hook's
    warnings go to stderr; --quiet keeps sensitive values out.

    Examples
    --------
    ```console
    $ untaped recipe hooks run ./acme/pin_python --target ./api --file ci.yml --diff
    ```
    """

    name: Positional[str] = Field(description="Hook name or PACK/HOOK reference.")
    target: Path = Field(description="Target directory.")
    project: Path | None = Field(
        None,
        description="Local hook project to run from (never adopted implicitly from the cwd).",
    )
    kind: HookContract | None = Field(
        None, description="The callable to run, for a hook that exports both."
    )
    file: Path | None = Field(None, description="Target-relative file path for transform hooks.")
    content: TextOrStdin | None = Field(
        None, description="Transform fixture content, or - to read it from stdin."
    )
    content_file: Path | None = Field(
        None, description="Read transform fixture content from a file."
    )
    var: list[KeyValue] = Field(
        default_factory=list,
        description="Hook input as KEY=YAML (repeatable; wins over --vars-file).",
    )
    vars_file: list[ExistingFile] = Field(
        default_factory=list,
        description="YAML mapping of hook inputs (repeatable; later files win).",
    )
    arg: list[KeyValue] = Field(
        default_factory=list,
        description="Hook arg as KEY=YAML (repeatable; wins over --args-file).",
    )
    args_file: list[ExistingFile] = Field(
        default_factory=list,
        description="YAML mapping of hook args (repeatable; later files win).",
    )
    diff: bool = Field(False, description="Print a unified diff for transform hooks.")
    hooks: Hooks = Field(default_factory=Hooks)
    output: Output[HookRun] = Output(render={"text": _text}, format="text")

    def run(self) -> HookRun:
        with recipe_errors():
            project, name = _split_ref(self.name, self.project)
            local = _local_project(project)
            ref = HookResolver(library_dir=self.root).resolve(name, local)
            kind = None if self.kind is None else self.kind.kind
            try:
                verb = select_verb(ref.exports, file_given=self.file is not None, kind=kind)
            except AmbiguousHookVerbError as exc:
                raise UsageError(
                    f"hook {name!r} exports both transform() and validate(); pass --kind or --file"
                ) from exc
            if verb == "validate" and self.diff:
                raise UsageError("validate hooks do not accept --file or content options")
            RunHook.validate_context(
                kind=verb,
                target=self.target,
                file=self.file,
                content=self.content,
                content_file=self.content_file,
            )
            inputs = merge_vars(
                self.vars_file, _yaml_pairs(self.var, flag="--var"), flag="--vars-file"
            )
            args = merge_vars(
                self.args_file, _yaml_pairs(self.arg, flag="--arg"), flag="--args-file"
            )
            with self.executor(self.hooks) as executor:
                execution = RunHook(executor).run(
                    name,
                    kind=verb,
                    local_hook_project=local,
                    target=self.target,
                    file=self.file,
                    content=self.content,
                    content_file=self.content_file,
                    inputs=inputs,
                    args=args,
                )
        file = execution.relative_file if isinstance(execution, TransformHookRun) else None
        self._context(execution.hook, kind=execution.kind, file=file, inputs=inputs, args=args)
        if execution.diagnostics:
            self.ctx.diag.relay(execution.diagnostics.rstrip())
        for warning in execution.warnings:
            self.ctx.diag.warning(warning)
        if isinstance(execution, TransformHookRun):
            return self._transformed(execution)
        return _verdict(execution)

    def _transformed(self, execution: TransformHookRun) -> HookRun:
        diff = (
            unified_diff_text(
                execution.before, execution.content, path=execution.relative_file.as_posix()
            )
            if self.diff
            else None
        )
        return HookRun(
            hook=execution.hook,
            kind="transform",
            target=str(execution.target),
            file=str(execution.relative_file),
            status="ok",
            content=execution.content,
            diff=diff,
        )

    def _context(
        self,
        hook: str,
        *,
        kind: str,
        file: Path | None,
        inputs: dict[str, object],
        args: dict[str, object],
    ) -> None:
        diag = self.ctx.diag
        diag.info(f"Hook run: {hook} ({kind})")
        diag.info(f"target: {self.target}")
        if file is not None:
            diag.info(f"file: {file}")
        diag.info(f"inputs: {json.dumps(inputs, default=str, sort_keys=True)}")
        diag.info(f"args: {json.dumps(args, default=str, sort_keys=True)}")


def _verdict(execution: ValidateHookRun) -> HookRun:
    """The verdict's row; a ``fail`` carries the error that makes the run exit 1."""
    verdict = execution.verdict
    error = None
    if verdict.failed:
        error = Failure.from_exception(RecipeError(verdict.message or "the hook's verdict is fail"))
    return HookRun(
        hook=execution.hook,
        kind="validate",
        target=str(execution.target),
        status=verdict.status,
        message=verdict.message,
        error=error,
    )


def _split_ref(name: str, project: Path | None) -> tuple[Path | None, str]:
    """Accept the ``./pack/hook`` form ``hooks init`` accepts.

    A path-shaped ref resolves as ``--project <parent>`` plus the trailing hook
    name; the two forms may not be combined.
    """
    if not is_path_ref(name):
        return project, name
    if project is not None:
        raise UsageError("pass the hook as a ./pack/hook path or with --project, not both")
    path = Path(name)
    if not path.name or path.parent in (Path("."), Path("")):
        raise UsageError("path hook refs must use <project>/<hook>")
    return path.parent, path.name


def _local_project(project: Path | None) -> Path | None:
    """The named local hook project, checked; never the cwd's, which would run whatever is there."""
    if project is None:
        return None
    resolved = project.expanduser().resolve()
    if not resolved.is_dir():
        raise PathNotFoundError(f"hook project not found: {project}")
    if not (resolved / "pyproject.toml").is_file():
        raise RecipeError(f"hook project has no pyproject.toml: {project}")
    if not read_hook_project(resolved).hooks:
        raise RecipeError(f"hook project has no hook metadata: {project}")
    return resolved


def _yaml_pairs(pairs: list[tuple[str, str]], *, flag: str) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        try:
            values[key] = yaml.safe_load(value)
        except yaml.YAMLError as exc:
            raise UsageError(f"{flag} value for {key!r} is invalid YAML: {exc}") from exc
    return values


class HooksGroup(Group):
    """Inspect, scaffold, and run recipe hooks."""

    commands = (List, Get, Edit, Init, Run)

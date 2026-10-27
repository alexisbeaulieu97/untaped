"""``untaped recipe test``: run the golden-fixture cases of installed or local packs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

from pydantic import Field

from untaped.sdk import (
    Document,
    Failed,
    Failure,
    Output,
    Positional,
    UntapedError,
    UsageError,
    unified_diff_text,
)
from untaped_recipe.application.harness import (
    CaseResult,
    DiscoveredCase,
    discover_cases,
    orphaned_test_dirs,
    run_case,
    update_case,
)
from untaped_recipe.application.resolution import find_library_recipe, resolve_explicit_recipe
from untaped_recipe.commands._recipe import RecipeCommand, recipe_errors
from untaped_recipe.domain.pack import InstalledPack
from untaped_recipe.domain.paths import is_path_ref
from untaped_recipe.domain.recipe import RecipeFile
from untaped_recipe.errors import RecipeError
from untaped_recipe.infrastructure.pack_store import PackLibrary
from untaped_recipe.records import Test as TestRow


@dataclass(frozen=True)
class _Selection:
    """Cases to run plus non-case rows the selection already produced."""

    cases: list[DiscoveredCase] = field(default_factory=list)
    static_results: list[CaseResult] = field(default_factory=list)
    packs_without_tests: list[str] = field(default_factory=list)


class Test(RecipeCommand):
    """Run golden-fixture test cases for packs.

    A case lives under `tests/<recipe>/<case>/` in the pack: `given/` is
    copied to a temporary target and `expected/` is the full expected tree
    (no `expected/` asserts no change). An optional, data-only `case.yml`
    sets `inputs`, `expect: success|error`, `error_contains` and `verdict`.
    --update needs an explicit pack or recipe. Any failing case exits 1, and
    so does an explicit ref with no cases. The table names the pack only when
    cases from several packs ran.
    """

    ref: Annotated[Positional[str | None], Document(RecipeFile)] = Field(
        None, description="Installed pack, recipe ref, or pack path."
    )
    update: bool = Field(False, description="Regenerate expected/ trees from the current plan.")
    output: Output[TestRow]

    def run(self) -> Iterator[TestRow]:
        if self.update and self.ref is None:
            raise UsageError("--update requires an explicit pack or recipe argument")
        with recipe_errors():
            selection = _select(self.library(), self.ref)
            results = self._execute(selection)
        if len({result.pack for result in results}) > 1:
            self.output.columns.insert(0, "pack")
        yield from (_row(result) for result in results)
        if not self.update:
            self._diffs(results)
        self._summary(selection, results)
        bad = {"error"} if self.update else {"fail", "error"}
        if any(result.status in bad for result in results):
            raise Failed("test cases failed")

    def _execute(self, selection: _Selection) -> list[CaseResult]:
        results = list(selection.static_results)
        if not selection.cases:
            return results
        runner = update_case if self.update else run_case
        with (
            self.executor() as executor,
            self.ctx.diag.progress("Running test cases") as progress,
        ):
            total = len(selection.cases)
            for index, case in enumerate(selection.cases, start=1):
                results.append(runner(case, executor=executor))
                progress.update(f"{case.recipe_name}/{case.case_name}", done=index, total=total)
        return results

    def _diffs(self, results: list[CaseResult]) -> None:
        for result in results:
            if not result.diffs:
                continue
            self.ctx.diag.info(f"# {result.pack}/{result.recipe}/{result.case}")
            for change in result.diffs:
                diff = unified_diff_text(
                    change.before, change.after, path=change.relative_path.as_posix()
                )
                if diff:
                    self.ctx.diag.info(diff.removesuffix("\n"))

    def _summary(self, selection: _Selection, results: list[CaseResult]) -> None:
        diag = self.ctx.diag
        count = {status: 0 for status in ("pass", "fail", "error", "updated")}
        for result in results:
            count[result.status] += 1
        if self.update:
            (diag.warning if count["error"] else diag.info)(
                f"Recipe test update: {count['updated']} updated, {count['pass']} unchanged, "
                f"{count['error']} errored",
            )
            return
        (diag.warning if count["fail"] or count["error"] else diag.info)(
            f"Recipe tests: {count['pass']} passed, {count['fail']} failed, "
            f"{count['error']} errored"
        )
        if selection.packs_without_tests:
            diag.info("packs without tests: " + ", ".join(selection.packs_without_tests))


def _select(library: PackLibrary, ref_text: str | None) -> _Selection:
    if ref_text is None:
        selection = _Selection()
        for pack in library.packs():
            if not (pack.root / "tests").is_dir():
                selection.packs_without_tests.append(pack.name)
                continue
            _extend_for_pack(selection, pack)
        return selection
    if ref_text.endswith((".yml", ".yaml")):
        if is_path_ref(ref_text):
            resolve_explicit_recipe(library, Path(ref_text).expanduser(), recipe_id=None)
        raise RecipeError("test requires a pack directory or ref, not a recipe file")
    if is_path_ref(ref_text):
        return _explicit(library.local_pack(Path(ref_text).expanduser()), recipe=None)
    found = library.find_pack(ref_text)
    if found is not None:
        return _explicit(found, recipe=None)
    recipe_pack, name, _entry = find_library_recipe(library, ref_text)
    return _explicit(recipe_pack, recipe=name)


def _explicit(pack: InstalledPack, *, recipe: str | None) -> _Selection:
    selection = _Selection()
    if recipe is None:
        _extend_for_pack(selection, pack)
    else:
        selection.cases.extend(discover_cases(pack, recipe=recipe))
    if not selection.cases and not selection.static_results:
        selection.static_results.append(
            CaseResult(
                pack=pack.name,
                recipe=recipe or "",
                case="",
                status="error",
                detail="no test cases found",
            )
        )
    return selection


def _extend_for_pack(selection: _Selection, pack: InstalledPack) -> None:
    selection.cases.extend(discover_cases(pack))
    selection.static_results.extend(
        CaseResult(
            pack=pack.name,
            recipe=name,
            case="",
            status="error",
            detail="tests directory names no known recipe",
        )
        for name in orphaned_test_dirs(pack)
    )


def _row(result: CaseResult) -> TestRow:
    """One case's row; an ``error`` case also carries a structured ``error``."""
    error = None
    if result.status == "error":
        # A plain exception (or none) is an invalid case or pack file.
        failure = result.failure
        if not isinstance(failure, UntapedError):
            failure = RecipeError(result.detail)
        error = Failure.from_exception(failure, message=result.detail)
    return TestRow(
        pack=result.pack,
        recipe=result.recipe,
        case=result.case,
        status=result.status,
        detail=result.detail,
        error=error,
    )

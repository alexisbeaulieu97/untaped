"""Execute resolved hooks in-process for built-ins or through uv workers."""

from __future__ import annotations

import traceback
from collections.abc import Callable
from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from types import ModuleType

from untaped.capabilities.recipe._worker import worker_protocol as protocol
from untaped.capabilities.recipe._worker.helpers import HookHelpers
from untaped.capabilities.recipe.domain.hook_project import HookKind, ensure_hook_supports
from untaped.capabilities.recipe.domain.plan import HookDebugResult, Verdict
from untaped.capabilities.recipe.infrastructure.hook_resolver import HookResolver, UvHookRef
from untaped.capabilities.recipe.infrastructure.hook_worker_client import (
    APPLY_DIAGNOSTIC_LIMIT,
    DEBUG_DIAGNOSTIC_LIMIT,
    DEBUG_DIAGNOSTIC_SETTLE_SECONDS,
    HookWorkerClient,
)


class HookExecutionError(RuntimeError):
    """Raised when a debug hook invocation fails inside hook code."""


@dataclass(frozen=True)
class _HookCall:
    """One hook invocation, callable in-process or sent to a worker."""

    verb: HookKind
    target: Path
    inputs: dict[str, object]
    args: dict[str, object]
    content: str = ""
    file: Path | None = None

    def invoke(self, module: ModuleType, hook: str, helpers: HookHelpers) -> object:
        function = getattr(module, self.verb, None)
        if function is None:
            raise ValueError(f"{self.verb} hook {hook!r} has no {self.verb} callable")
        if self.verb == "transform":
            return function(
                self.content,
                inputs=self.inputs,
                target=self.target,
                file=self.file,
                args=self.args,
                helpers=helpers,
            )
        return function(inputs=self.inputs, target=self.target, args=self.args, helpers=helpers)

    def payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            protocol.KIND: self.verb,
            protocol.INPUTS: self.inputs,
            protocol.TARGET: str(self.target),
            protocol.ARGS: self.args,
        }
        if self.verb == "transform":
            payload[protocol.CONTENT] = self.content
            payload[protocol.FILE] = str(self.file)
        return payload


class HookExecutor:
    """Dispatch hook calls through the correct runtime."""

    def __init__(self, resolver: HookResolver, *, workers: HookWorkerClient) -> None:
        self._resolver = resolver
        self._workers = workers

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""
        call = _HookCall("transform", target, inputs, args, content=content, file=file)
        execution = self._invoke(hook, local_hook_project, call, capture_diagnostics)
        if not isinstance(execution.result, str):
            raise ValueError(f"transform hook {hook!r} must return str")
        return HookDebugResult(
            result=execution.result,
            diagnostics=execution.diagnostics,
            warnings=execution.warnings,
        )

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return its coerced verdict plus diagnostics."""
        call = _HookCall("validate", target, inputs, args)
        execution = self._invoke(hook, local_hook_project, call, capture_diagnostics)
        return HookDebugResult(
            result=_coerce_verdict(execution.result),
            diagnostics=execution.diagnostics,
            warnings=execution.warnings,
        )

    def _invoke(
        self,
        hook: str,
        local_hook_project: Path | None,
        call: _HookCall,
        capture_diagnostics: bool,
    ) -> HookDebugResult[object]:
        ref = self._resolver.resolve(hook, local_hook_project)
        ensure_hook_supports(ref.exports, hook, verb=call.verb)
        if isinstance(ref, UvHookRef):
            return _request_external(
                self._workers,
                ref,
                call.payload(),
                capture_diagnostics=capture_diagnostics,
            )
        # A fresh helpers instance per call keeps warn() isolated per target
        # (and per planning thread).
        helpers = HookHelpers()
        module = ref.module
        execution = _call_builtin_with_capture(
            lambda: call.invoke(module, hook, helpers),
            capture_diagnostics=capture_diagnostics,
        )
        return HookDebugResult(
            result=execution.result,
            diagnostics=execution.diagnostics,
            warnings=tuple(helpers.warnings),
        )


def _call_builtin_with_capture(
    call: Callable[[], object],
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    if not capture_diagnostics:
        return HookDebugResult(result=call(), diagnostics="")
    stdout = StringIO()
    try:
        with redirect_stdout(stdout):
            result = call()
    except Exception as exc:
        raise HookExecutionError(traceback.format_exc().rstrip()) from exc
    return HookDebugResult(result=result, diagnostics=stdout.getvalue().strip())


def _request_external(
    workers: HookWorkerClient,
    ref: UvHookRef,
    payload: dict[str, object],
    *,
    capture_diagnostics: bool,
) -> HookDebugResult[object]:
    diagnostic_limit = DEBUG_DIAGNOSTIC_LIMIT if capture_diagnostics else APPLY_DIAGNOSTIC_LIMIT
    settle_seconds = DEBUG_DIAGNOSTIC_SETTLE_SECONDS if capture_diagnostics else 0
    try:
        worker_result = workers.request(
            ref,
            payload,
            diagnostic_limit=diagnostic_limit,
            settle_seconds=settle_seconds,
        )
    except Exception as exc:
        if not capture_diagnostics:
            raise
        raise HookExecutionError(str(exc)) from exc
    return HookDebugResult(
        result=worker_result.result,
        diagnostics=worker_result.diagnostics if capture_diagnostics else "",
        warnings=worker_result.warnings,
    )


def _coerce_verdict(value: object) -> Verdict:
    """Coerce a raw validate result into the current verdict model."""
    if isinstance(value, Verdict):
        return value
    if isinstance(value, dict):
        return Verdict.model_validate(value)
    if value is None:
        return Verdict(status="pass")
    if isinstance(value, str):
        return Verdict(status="fail", message=value)
    raise ValueError(f"invalid validate verdict: {value!r}")

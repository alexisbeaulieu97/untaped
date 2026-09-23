"""Stdlib-only NDJSON worker for uv-managed external hook projects."""

from __future__ import annotations

import importlib
import json
import sys
import traceback
from collections.abc import Mapping
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any


def _load_sibling(name: str) -> Any:  # pragma: no cover - script mode only
    """Load a sibling worker module by file path under a private module name.

    The worker runs with ``python -P`` so its directory is not on ``sys.path``;
    loading siblings by path keeps a pack's own modules from shadowing them
    (and them from shadowing the pack's).
    """
    import importlib.util  # noqa: PLC0415

    spec = importlib.util.spec_from_file_location(
        f"_untaped_recipe_worker_{name}", Path(__file__).with_name(f"{name}.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load worker module {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


if __package__:
    from untaped.capabilities.recipe._worker import helpers as hook_helpers
    from untaped.capabilities.recipe._worker import worker_protocol as protocol
else:  # pragma: no cover - used when executed as a script in a hook env.
    protocol = _load_sibling("worker_protocol")
    hook_helpers = _load_sibling("helpers")


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one decoded worker request."""
    request_id = _required_str(request, protocol.ID)
    kind = _required_str(request, protocol.KIND)
    module_name = _required_str(request, protocol.MODULE)
    with redirect_stdout(sys.stderr):
        module = importlib.import_module(module_name)
    helpers = hook_helpers.HookHelpers()
    if kind == protocol.TRANSFORM:
        transform = getattr(module, "transform", None)
        if transform is None:
            raise ValueError(f"transform hook module {module_name!r} has no transform callable")
        with redirect_stdout(sys.stderr):
            result = transform(
                _required_str(request, protocol.CONTENT),
                inputs=_mapping(request.get(protocol.INPUTS), protocol.INPUTS),
                target=Path(_required_str(request, protocol.TARGET)),
                file=Path(_required_str(request, protocol.FILE)),
                args=_mapping(request.get(protocol.ARGS), protocol.ARGS),
                helpers=helpers,
            )
        if not isinstance(result, str):
            raise ValueError("transform hook must return str")
        return {
            protocol.ID: request_id,
            protocol.OK: True,
            protocol.RESULT: result,
            protocol.WARNINGS: helpers.warnings,
        }
    if kind == protocol.VALIDATE:
        validate = getattr(module, "validate", None)
        if validate is None:
            raise ValueError(f"validate hook module {module_name!r} has no validate callable")
        with redirect_stdout(sys.stderr):
            result = validate(
                inputs=_mapping(request.get(protocol.INPUTS), protocol.INPUTS),
                target=Path(_required_str(request, protocol.TARGET)),
                args=_mapping(request.get(protocol.ARGS), protocol.ARGS),
                helpers=helpers,
            )
        return {
            protocol.ID: request_id,
            protocol.OK: True,
            protocol.RESULT: _wire_value(result),
            protocol.WARNINGS: helpers.warnings,
        }
    raise ValueError(f"unsupported hook request kind: {kind}")


def main() -> int:
    """Run the NDJSON worker loop."""
    _configure_standard_streams()
    # The ready line ends the client's startup wait: everything before it
    # (uv env creation on first use, module imports) is environment setup,
    # charged against the startup bound rather than the per-hook timeout.
    sys.stdout.write(json.dumps({protocol.READY: True}) + "\n")
    sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request_id = ""
        try:
            decoded = json.loads(line)
            if not isinstance(decoded, dict):
                raise ValueError("worker request must be a JSON object")
            request_id = str(decoded.get(protocol.ID, ""))
            response = handle_request(decoded)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            response = {
                protocol.ID: request_id,
                protocol.OK: False,
                protocol.ERROR: f"{type(exc).__name__}: {exc}",
            }
        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()
    return 0


def _configure_standard_streams() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _required_str(request: dict[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str):
        raise ValueError(f"worker request field {key!r} must be a string")
    return value


def _mapping(value: object, field: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"worker request field {field!r} must be an object")
    return dict(value)


def _wire_value(value: object) -> object:
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _json_safe_mapping(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if not isinstance(dumped, Mapping):
            raise ValueError(f"invalid validate verdict: {value!r}")
        return _json_safe_mapping(dumped)
    raise ValueError(f"invalid validate verdict: {value!r}")


def _json_safe_mapping(value: Mapping[object, object]) -> dict[str, object]:
    result = {str(key): item for key, item in value.items()}
    try:
        json.dumps(result)
    except TypeError as exc:
        raise ValueError(f"invalid validate verdict: {exc}") from exc
    return result


if __name__ == "__main__":
    raise SystemExit(main())

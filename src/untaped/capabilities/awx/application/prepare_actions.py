"""Validate a complete action selection and freeze inventory source expansion."""

import json
from collections.abc import Mapping, Sequence
from typing import Any

from untaped.capabilities.awx.application.ports import Catalog, ResourceClient
from untaped.capabilities.awx.application.selection import (
    SelectedResource,
    SelectionRequest,
    SelectionResolver,
)
from untaped.capabilities.awx.domain import ResourceSpec
from untaped.capabilities.awx.errors import AwxApiError, LaunchPromptError

# Launch payload field → (template prompt flag, CLI flag that sets it).
LAUNCH_PROMPTS: dict[str, tuple[str, str]] = {
    "extra_vars": ("ask_variables_on_launch", "--extra-vars"),
    "limit": ("ask_limit_on_launch", "--limit"),
    "inventory": ("ask_inventory_on_launch", "--inventory"),
    "credentials": ("ask_credential_on_launch", "--credential"),
    "scm_branch": ("ask_scm_branch_on_launch", "--scm-branch"),
    "job_tags": ("ask_tags_on_launch", "--job-tag"),
    "skip_tags": ("ask_skip_tags_on_launch", "--skip-tag"),
    "verbosity": ("ask_verbosity_on_launch", "--verbosity"),
    "diff_mode": ("ask_diff_mode_on_launch", "--diff-mode"),
    "job_type": ("ask_job_type_on_launch", "--job-type"),
}


def _preflight_launch(
    client: ResourceClient,
    spec: ResourceSpec,
    item: SelectedResource,
    payload: Mapping[str, Any],
) -> None:
    info = client.sub_endpoint_request(spec, item.id, "launch", "GET")
    label = f"{spec.kind} {item.name!r} (id={item.id})"
    for field in payload:
        prompt = LAUNCH_PROMPTS.get(field)
        if prompt is None:
            continue
        ask_key, flag = prompt
        if info.get(ask_key) is False and not (
            field == "extra_vars" and info.get("survey_enabled")
        ):
            raise LaunchPromptError(
                f"{label} does not prompt for {field} on launch ({ask_key} is false); "
                f"AWX would ignore {flag}. Enable {ask_key} on the template or drop {flag}."
            )
    needed = info.get("variables_needed_to_start") or []
    supplied = _extra_var_names(payload.get("extra_vars"))
    missing = [name for name in needed if name not in supplied]
    if missing:
        raise LaunchPromptError(
            f"{label} requires survey variables {', '.join(map(str, missing))}; "
            "pass them with --extra-vars KEY=VAL."
        )


def _extra_var_names(value: Any) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return set()
    return set(value) if isinstance(value, dict) else set()


def prepare_action_targets(
    client: ResourceClient,
    catalog: Catalog,
    spec: ResourceSpec,
    selected: Sequence[SelectedResource],
    *,
    action: str,
    payload: Mapping[str, Any] | None = None,
) -> tuple[ResourceSpec, tuple[SelectedResource, ...]]:
    """Resolve every eligible source before any POST, never server-side aggregate sync.

    Launches are preflighted against ``GET <template>/launch/`` so a field
    the template does not prompt for (AWX silently ignores it) or a missing
    required survey variable fails the whole selection before any POST.
    """
    if not selected:
        raise AwxApiError(f"No {spec.kind} targets selected for {action}")
    if action == "launch":
        for item in selected:
            _preflight_launch(client, spec, item, payload or {})
    if action != "sync":
        return spec, tuple(selected)
    targets = tuple(selected)
    if spec.kind == "Inventory":
        source_spec = catalog.get("InventorySource")
        expanded: dict[int, SelectedResource] = {}
        for item in selected:
            label = f"Inventory {item.name!r} (id={item.id})"
            if item.record.get("kind", "") not in ("", "constructed"):
                raise AwxApiError(f"{label}: sync is unsupported for {item.record.get('kind')}")
            sources = SelectionResolver(client, catalog).resolve(
                source_spec,
                SelectionRequest(filters={"inventory": str(item.id)}, mutation=True),
            )
            if not sources:
                raise AwxApiError(f"{label}: no inventory sources to sync")
            expanded.update((source.id, source) for source in sources)
        spec, targets = source_spec, tuple(expanded.values())
    for item in targets:
        if spec.kind == "InventorySource" and item.record.get("source") in (None, "", "file"):
            raise AwxApiError(f"InventorySource {item.name!r} (id={item.id}): no syncable source")
        if spec.kind == "Project" and not item.record.get("scm_type"):
            raise AwxApiError(f"Project {item.name!r} (id={item.id}): manual project cannot sync")
    return spec, targets

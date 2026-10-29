"""``source patch``'s flags, and the source they make of a saved one."""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError

from untaped.sdk import UsageError, first_validation_error
from untaped_ansible.commands._ansible import REF_SCAN_DEFAULT, RefScanDefault
from untaped_ansible.errors import AnsibleError
from untaped_ansible.settings import SourceDefinition, normalize_team_refs


def _add(what: str) -> list[str]:
    return Field(default_factory=list, description=f"{what} to add.")


def _remove(what: str) -> list[str]:
    return Field(default_factory=list, description=f"{what} to remove.")


class Changes(BaseModel):
    """What ``source patch`` adds to, removes from or clears in a saved source."""

    add_org: list[str] = _add("GitHub org")
    remove_org: list[str] = _remove("GitHub org")
    clear_org: bool = Field(False, description="Remove every org.")
    add_team: list[str] = _add("GitHub team as ORG/SLUG (or SLUG with one org)")
    remove_team: list[str] = _remove("GitHub team as ORG/SLUG (or SLUG with one org)")
    clear_team: bool = Field(False, description="Remove every team.")
    add_repo: list[str] = _add("GitHub repo as owner/name")
    remove_repo: list[str] = _remove("GitHub repo as owner/name")
    clear_repo: bool = Field(False, description="Remove every repo.")
    add_path: list[str] = _add("Dependency file path")
    remove_path: list[str] = _remove("Dependency file path")
    clear_path: bool = Field(False, description="Remove every dependency file path.")
    add_ref_kind: list[str] = _add("Ref namespace (heads or tags)")
    remove_ref_kind: list[str] = _remove("Ref namespace")
    clear_ref_kind: bool = Field(False, description="Remove every ref namespace.")
    add_ref_pattern: list[str] = _add("fnmatch pattern for branch/tag names")
    remove_ref_pattern: list[str] = _remove("fnmatch pattern for branch/tag names")
    clear_ref_pattern: bool = Field(False, description="Remove every ref pattern.")
    ref_scan_default: RefScanDefault | None = Field(None, description=REF_SCAN_DEFAULT)
    clear_ref_scan_default: bool = Field(
        False,
        description="Drop the per-source scan strategy and fall back to ansible.ref_scan_default.",
    )


def edited(source: SourceDefinition, edit: Changes) -> tuple[SourceDefinition, list[str]]:
    """``source`` with ``edit`` applied, and what changed, one line each."""
    if not any(edit.model_dump().values()):
        raise UsageError("source patch requires at least one mutation flag")
    if edit.ref_scan_default is not None and edit.clear_ref_scan_default:
        raise UsageError("--ref-scan-default cannot be combined with --clear-ref-scan-default")
    changes: list[str] = []
    name = source.name

    def apply(
        label: str, current: list[str], add: list[str], remove: list[str], clear: bool
    ) -> list[str]:
        return _apply(name, label, current, add=add, remove=remove, clear=clear, changes=changes)

    orgs = apply("org", source.orgs, edit.add_org, edit.remove_org, edit.clear_org)
    teams = apply(
        "team",
        source.teams,
        _teams(edit.add_team, orgs),
        _teams(edit.remove_team, source.orgs),
        edit.clear_team,
    )
    repos = apply("repo", source.repos, edit.add_repo, edit.remove_repo, edit.clear_repo)
    paths = apply("path", source.dependency_paths, edit.add_path, edit.remove_path, edit.clear_path)
    ref_kinds = apply(
        "ref-kind", source.ref_kinds, edit.add_ref_kind, edit.remove_ref_kind, edit.clear_ref_kind
    )
    ref_patterns = apply(
        "ref-pattern",
        source.ref_patterns,
        edit.add_ref_pattern,
        edit.remove_ref_pattern,
        edit.clear_ref_pattern,
    )
    scan = source.ref_scan_default
    if edit.clear_ref_scan_default and scan is not None:
        scan = None
        changes.append("cleared ref-scan-default")
    if edit.ref_scan_default is not None and scan != edit.ref_scan_default:
        scan = edit.ref_scan_default
        changes.append(f"set ref-scan-default {scan}")
    try:
        result = SourceDefinition(
            name=name,
            orgs=orgs,
            teams=teams,
            repos=repos,
            dependency_paths=paths,
            ref_kinds=ref_kinds,
            ref_patterns=ref_patterns,
            ref_scan_default=scan,
        )
    except ValidationError as exc:
        # The flags made it, as source set's do: the same usage error.
        raise UsageError(first_validation_error(exc)) from exc
    return result, changes


def _apply(
    source_name: str,
    label: str,
    current: list[str],
    *,
    add: list[str],
    remove: list[str],
    clear: bool,
    changes: list[str],
) -> list[str]:
    edited = list(current)
    if clear and edited:
        edited = []
        changes.append(f"cleared {label}")
    for value in remove:
        if value not in edited:
            raise AnsibleError(
                f"source {source_name!r} has no {label} {value}", category="not_found"
            )
        edited.remove(value)
        changes.append(f"removed {label} {value}")
    for value in add:
        if value in edited:
            continue
        edited.append(value)
        changes.append(f"added {label} {value}")
    return edited


def _teams(values: list[str], orgs: list[str]) -> list[str]:
    if not values:
        return values
    try:
        return normalize_team_refs(values, orgs)
    except ValueError as exc:
        raise AnsibleError(str(exc), category="invalid") from exc

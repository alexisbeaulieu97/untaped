"""Generate ``docs/reference/config.md`` from the composed settings models.

The page lists every setting of the root shell (``log_level``, ``http.*``,
``ui.*``) and of each built-in capability's profile model, plus each
capability's state model. Types, defaults and environment variables come from
the Pydantic models; a description comes from ``Field(description=...)`` when
the model declares one, else from :data:`DESCRIPTIONS` below.

Usage::

    uv run python scripts/gen_config_reference.py          # rewrite the page
    uv run python scripts/gen_config_reference.py --check  # exit 1 if stale

``tests/unit/test_config_reference.py`` fails when the checked-in page is
stale or a setting has no description.
"""

from __future__ import annotations

import sys
import types
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, SecretStr
from pydantic_core import PydanticUndefined

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "reference" / "config.md"

#: Descriptions for settings whose model field has no ``description``.
DESCRIPTIONS: dict[str, str] = {
    "log_level": "Deprecated and ignored (removed in 7.0); `untaped doctor` warns when set.",
    "http.ca_bundle": "PEM file of extra CA certificates to trust instead of the OS trust store.",
    "http.verify_ssl": "Verify TLS certificates. `false` disables all certificate checks.",
    "http.verify_hostname": "Check the certificate host name. `false` keeps chain validation.",
    "http.timeout": "HTTP request timeout in seconds.",
    "http.proxy": "Proxy URL for HTTP clients. When unset, standard proxy variables apply.",
    "ui.theme": "Built-in theme: `default`, `plain`, `compact`, `high-contrast`, `quiet`, "
    "`classic`.",
    "ui.border": "Table border style; overrides the theme.",
    "ui.density": "Table density; overrides the theme.",
    "ui.collection_view": "How lists render in `table` format; overrides the theme.",
    "ui.detail_view": "How a single record renders in `table` format; overrides the theme.",
    "ui.symbols": "Symbol overrides merged over the theme's symbols.",
    "ui.color_roles": "Color-role overrides merged over the theme's colors.",
    "workspace.cache_dir": "Bare-clone cache used as the reference for new workspace clones.",
    "workspace.workspaces_dir": "Parent directory for `workspace init NAME` without `--path`.",
    "workspace.workspaces": "Registered workspaces (`name`, `path`). Managed by `workspace` "
    "commands.",
    "github.base_url": "GitHub API URL. GitHub Enterprise Server uses `https://HOST/api/v3`.",
    "github.token": "GitHub token for API calls and Git fetches.",
    "github.corpus_path": "Local Git corpus that `github sweep` and `github cache` manage.",
    "github.sweep.max_age_seconds": "`sweep` refreshes cached repos older than this.",
    "github.sweep.sync_concurrency": "Default `sweep --parallel` Git workers.",
    "jira.base_url": "Jira Data Center URL, for example `https://jira.example.com`.",
    "jira.token": "Jira personal access token.",
    "jira.api_prefix": "Jira platform REST prefix.",
    "jira.agile_prefix": "Jira Software (boards, sprints) REST prefix.",
    "jira.assigned_jql": "Base JQL for `issues assigned`, and for `issues search` with no query.",
    "jira.default_project": "Project key `issues create` uses when `--project` is omitted.",
    "jira.default_board_id": "Board `sprints list` uses when `--board-id` is omitted.",
    "jira.page_size": "Results requested per Jira API page.",
    "awx.base_url": "AWX/AAP URL, for example `https://aap.example.com`.",
    "awx.token": "AWX/AAP API token.",
    "awx.api_prefix": "API prefix. Standalone AWX usually uses `/api/v2/`.",
    "awx.default_organization": "Organization that scopes name lookups and `apply` documents "
    "without one.",
    "awx.page_size": "Results requested per AWX API page.",
    "ansible.index_path": "SQLite cache of refreshed source data.",
    "ansible.stale_after": "Seconds after which `source status` reports a source as `stale`.",
    "ansible.freshness_ttl": "Deprecated and ignored; `doctor` warns while it is set.",
    "ansible.ref_scan_default": "Refs a source scans: `all` refs or each repo's default branch.",
    "ansible.source_refresh_backend": "Ref probe backend for source refresh.",
    "ansible.repo_cache_path": "Git clone cache used by source refresh.",
    "ansible.git_clone_protocol": "Protocol for source refresh clones.",
    "ansible.git_fetch_depth": "Git fetch depth for source refresh; `0` is full history.",
    "ansible.git_fetch_concurrency": "Default `--parallel` for `source refresh` and `graph`.",
    "ansible.probe_concurrency": "Concurrent ref probes during source refresh.",
    "ansible.source_refresh_repo_batch_size": "Repos committed per source refresh batch.",
    "ansible.source_refresh_rate_limit_floor": "Stop a refresh (resumable) when the GraphQL "
    "budget drops below this.",
    "ansible.git_blob_filter": "Fetch with a blob filter to download less.",
    "ansible.dependency_paths": "Dependency files scanned in each repo.",
    "ansible.sources": "Saved sources. Managed by `ansible source` commands.",
    "ansible.aliases": "Role or Galaxy name to `owner/repo` aliases. Managed by "
    "`ansible alias` commands.",
    "recipe.library_root": "Directory holding installed recipe packs.",
    "recipe.hook_timeout_seconds": "Per-hook request timeout; `0` disables it.",
    "recipe.hook_startup_timeout_seconds": "Timeout for preparing a hook environment.",
    "recipe.backup_keep": "`backup prune` keeps this many newest bundles by default.",
    "recipe.backup_max_age_days": "`backup prune` deletes bundles older than this by default.",
    "recipe.preview_max_rows": "Preview rows before `apply` collapses per-file rows; `0` is "
    "unlimited.",
}

_HEADER = """\
<!-- Generated by scripts/gen_config_reference.py. Do not edit by hand. -->

# Configuration reference

Every setting `untaped` reads, generated from the settings models. Profile
settings live under `profiles.<name>.<section>` in `~/.untaped/config.yml`;
state lives in `~/.untaped/state.yml` and is written only by the owning
capability's commands. See [Configuration](../configuration.md) for the file
layout, profiles and precedence.

Set a profile setting with `untaped config set KEY VALUE` (secrets:
`untaped config set KEY --prompt`). A `mapping` or `list` setting takes its
whole value as JSON or YAML (`untaped config set ui.symbols '{"ok": "+"}'`),
and `config unset` removes the whole key. `config set` and `config unset`
print an `untaped.setting_outcome` record and accept `--dry-run`. Each
profile setting can be overridden for one process with the environment
variable shown.
"""

_FOOTER = """\
## See also

- [Configuration](../configuration.md)
- [Environment variables](./environment.md)
"""


@dataclass(frozen=True)
class Row:
    """One documented setting."""

    key: str
    annotation: Any
    default: Any
    description: str | None


def _leaf_rows(model: type[BaseModel], prefix: tuple[str, ...]) -> list[Row]:
    """One :class:`Row` per leaf field, recursing into nested models."""
    rows: list[Row] = []
    for name, field in model.model_fields.items():
        path = (*prefix, name)
        annotation = _unwrap_optional(field.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            rows.extend(_leaf_rows(annotation, path))
            continue
        if field.default is not PydanticUndefined:
            default: Any = field.default
        elif field.default_factory is not None:
            default = field.default_factory()  # type: ignore[call-arg]
        else:
            default = None
        key = ".".join(path)
        rows.append(Row(key, field.annotation, default, field.description or DESCRIPTIONS.get(key)))
    return rows


def _unwrap_optional(annotation: Any) -> Any:
    if get_origin(annotation) in (typing.Union, types.UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _type_name(annotation: Any) -> str:
    optional = get_origin(annotation) in (typing.Union, types.UnionType) and type(None) in get_args(
        annotation
    )
    inner = _unwrap_optional(annotation)
    origin = get_origin(inner)
    if inner is SecretStr:
        name = "secret"
    elif origin is Literal:
        name = " \\| ".join(f"`{arg}`" for arg in get_args(inner))
    elif origin is list:
        name = "list"
    elif origin is dict:
        name = "mapping"
    elif inner is Path:
        name = "path"
    elif isinstance(inner, type):
        name = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(
            inner.__name__, inner.__name__
        )
    else:
        name = str(inner)
    return f"{name} (optional)" if optional else name


def _default_text(value: Any) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, (list, dict)):
        if not value:
            return "empty"
        return "; ".join(f"`{item}`" for item in value) if isinstance(value, list) else "built-in"
    return f"`{value}`"


def _env_name(key: str) -> str:
    return "UNTAPED_" + key.replace(".", "__").upper()


def collect_sections() -> list[tuple[str, str, type[BaseModel], bool]]:
    """``(title, prefix, model, is_state)`` for the shell and every built-in."""
    from untaped.bootstrap import BUILTIN_CAPABILITIES  # noqa: PLC0415
    from untaped.settings import Settings  # noqa: PLC0415

    sections: list[tuple[str, str, type[BaseModel], bool]] = [("Root", "", Settings, False)]
    for spec in BUILTIN_CAPABILITIES:
        sections.append(
            (f"`{spec.config_section}`", spec.config_section, spec.profile_model, False)
        )
        if spec.state_model is not None:
            sections.append(
                (f"`{spec.config_section}` state", spec.config_section, spec.state_model, True)
            )
    return sections


def missing_descriptions() -> list[str]:
    """Settings keys that have neither a field description nor a fallback."""
    return [row.key for row in _all_rows() if not row.description]


def unknown_descriptions() -> list[str]:
    """Fallback description keys that name no current setting."""
    known = {row.key for row in _all_rows()}
    return sorted(set(DESCRIPTIONS) - known)


def _all_rows() -> list[Row]:
    rows: list[Row] = []
    for _, prefix, model, _ in collect_sections():
        rows.extend(_section_rows(prefix, model))
    return rows


def _section_rows(prefix: str, model: type[BaseModel]) -> list[Row]:
    rows = _leaf_rows(model, (prefix,) if prefix else ())
    if not prefix:
        # Root model: keep the shell's own settings, not the pydantic-settings base.
        rows = [row for row in rows if row.key.split(".")[0] in {"log_level", "http", "ui"}]
    return rows


def render() -> str:
    """Return the full Markdown page."""
    parts = [_HEADER]
    for title, prefix, model, is_state in collect_sections():
        rows = _section_rows(prefix, model)
        if not rows:
            continue
        parts.append(f"## {title}\n")
        if is_state:
            parts.append("| Key | Type | Description |\n|---|---|---|")
            for row in rows:
                parts.append(f"| `{row.key}` | {_type_name(row.annotation)} | {row.description} |")
        else:
            parts.append(
                "| Key | Type | Default | Environment | Description |\n|---|---|---|---|---|"
            )
            for row in rows:
                parts.append(
                    f"| `{row.key}` | {_type_name(row.annotation)} | {_default_text(row.default)} "
                    f"| `{_env_name(row.key)}` | {row.description} |"
                )
        parts.append("")
    parts.append(_FOOTER)
    return "\n".join(parts)


def main(argv: list[str]) -> int:
    """Write the page, or with ``--check`` report whether it is current."""
    page = render()
    if "--check" in argv:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != page:
            print(f"{OUTPUT} is stale; run: uv run python scripts/gen_config_reference.py")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(page, encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

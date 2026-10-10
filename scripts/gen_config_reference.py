"""Generate ``docs/reference/config.md`` from the composed settings models.

The page lists every setting of the root app (``http.*``, ``ui.*``,
``skills.*``) and of each first-party plugin's settings model, plus each
plugin's state model. Types, defaults and environment variables come from
the Pydantic models; a description comes from ``Field(description=...)`` when
the model declares one, else from :data:`DESCRIPTIONS` below.

Usage::

    uv run python scripts/gen_config_reference.py          # rewrite the page
    uv run python scripts/gen_config_reference.py --check  # exit 1 if stale

``tests/repo/test_docs.py`` fails when the checked-in page is
stale or a setting has no description.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, get_args, get_origin

from packaging.utils import canonicalize_name
from pydantic import BaseModel, SecretStr
from release import packages

from untaped.config_schema import walk_settings
from untaped.theme import ROLE_NAMES, SYMBOL_NAMES

if TYPE_CHECKING:
    from untaped.stability import Stability

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "reference" / "config.md"

#: Descriptions for settings whose model field has no ``description``.
DESCRIPTIONS: dict[str, str] = {
    "http.ca_bundle": "PEM file of extra CA certificates to trust instead of the OS trust store.",
    "http.verify_ssl": "Verify TLS certificates. `false` disables all certificate checks.",
    "http.verify_hostname": "Check the certificate host name. `false` keeps chain validation.",
    "http.timeout_seconds": "HTTP request timeout in seconds.",
    "http.proxy": "Proxy URL for HTTP clients, and for Git fetches from the GitHub host. "
    "When unset, standard proxy variables (and, for Git, your Git config) apply.",
    "ui.format": "Default `--format` for commands whose default is `table`. "
    "`UNTAPED_FORMAT` wins over it; an explicit `--format` wins over both.",
    "ui.theme": "Built-in theme: `default`, `plain`, `compact`, `high-contrast`, `quiet`, "
    "`classic`.",
    "ui.border": "Table border style; overrides the theme.",
    "ui.density": "Table density; overrides the theme.",
    "ui.collection_view": "How lists render in `table` format; overrides the theme.",
    "ui.detail_view": "How a single record renders in `table` format; overrides the theme.",
    "ui.hide_empty_columns": "Leave out `table` columns that are empty on every row "
    "(on unless the theme turns it off); a column named in `--columns` always shows.",
    "ui.symbols": "Symbol overrides merged over the theme's symbols. Names: "
    + ", ".join(f"`{name}`" for name in SYMBOL_NAMES)
    + ".",
    "ui.color_roles": "Color-role overrides merged over the theme's colors. Names: "
    + ", ".join(f"`{name}`" for name in ROLE_NAMES)
    + ".",
    "skills.updates": "What each run does when installed agent skills differ from this "
    "version: `warn` (print a warning), `auto` (update them in place), or `off`.",
    "workspace.cache_dir": "Bare-clone cache that workspace worktrees are created from. "
    "Worktrees depend on it: don't delete it while workspaces are active.",
    "workspace.workspaces_dir": "Parent directory of every workspace (`<workspaces_dir>/NAME`).",
    "workspace.parallel": "Default workers for `create`/`add`/`run` and for status/archive "
    "checks. Unset means `min(8, 2 * CPUs)`; "
    "values above `2 * CPUs` are clamped.",
    "workspace.branch_template": "Branch name for writable repos; `{name}` is the workspace name.",
    "workspace.protocol": "Clone URL the GitHub inventory supplies: `https` or `ssh`.",
    "workspace.active": "Active workspaces. Managed by `workspace` commands.",
    "workspace.archived": "Archived workspaces. Managed by `workspace` commands.",
    "github.base_url": "GitHub API URL. GitHub Enterprise Server uses `https://HOST/api/v3`.",
    "github.token": "GitHub token for API calls and Git fetches. Falls back to "
    "`token_command`, then `GH_TOKEN`, then `GITHUB_TOKEN`.",
    "github.token_command": "Command (argv list, no shell) that prints the token; "
    "used when `github.token` is unset.",
    "github.default_org": "Org scope for `repos list`, `search` (repos, code, issues), "
    "`sweep`, `cache sync` and `cache prune` when no scope flag is given. Without it, "
    "search uses `@me`.",
    "github.git_protocol": "How `sweep` and `cache sync` fetch repos on the GitHub host: "
    "`https` or `ssh` (`git@HOST:OWNER/NAME.git`).",
    "github.sweep.max_age_seconds": "`sweep` and `cache sync` refresh cached repos older than "
    "this that GitHub reports as pushed since.",
    "github.sweep.parallel": "Default `sweep --parallel` and `cache sync --parallel` Git workers.",
    "github.inventory.path": "Cached repository list (metadata only) that workspace "
    "`create`/`add` resolve names from and the picker searches.",
    "github.inventory.orgs": "Orgs whose repositories the inventory lists. With no orgs "
    "or teams, `github.default_org`.",
    "github.inventory.teams": "Teams (`ORG/SLUG`, or `SLUG` in the one inventory org, else "
    "in `github.default_org`) whose repositories the inventory lists.",
    "github.inventory.max_age_seconds": "Refresh the inventory when it is older than this.",
    "jira.base_url": "Jira Data Center URL, for example `https://jira.example.com`.",
    "jira.token": "Jira personal access token. Falls back to `token_command`, then "
    "`JIRA_API_TOKEN`.",
    "jira.token_command": "Command (argv list, no shell) that prints the token; "
    "used when `jira.token` is unset.",
    "jira.api_prefix": "Jira platform REST prefix.",
    "jira.agile_prefix": "Jira Software (boards, sprints) REST prefix.",
    "jira.assigned_jql": "Base JQL for `issues assigned`, and for `issues search` with no query.",
    "jira.default_project": "Project key `issues create` uses when `--project` is omitted.",
    "jira.default_board_id": "Board `sprints list` uses when `--board-id` is omitted.",
    "jira.page_size": "Results requested per Jira API page.",
    "jira.confirm": "Which writes ask first: `always`, `destructive` (patches that replace or "
    "remove values, assignee changes, transitions) or `never`. `--yes` skips the prompt.",
    "awx.base_url": "AWX/AAP URL, for example `https://aap.example.com`.",
    "awx.token": "AWX/AAP API token. Falls back to `token_command`, then "
    "`CONTROLLER_OAUTH_TOKEN`, `TOWER_OAUTH_TOKEN`, then `AAP_TOKEN`.",
    "awx.token_command": "Command (argv list, no shell) that prints the token; "
    "used when `awx.token` is unset.",
    "awx.api_prefix": "API prefix. Standalone AWX usually uses `/api/v2/`.",
    "awx.default_organization": "Organization that scopes name lookups and `apply` documents "
    "without one.",
    "awx.page_size": "Results requested per AWX API page.",
    "awx.test_timeout_seconds": "Seconds a `test run` case waits before its job is cancelled, "
    "unless `--timeout`, the case's `timeout:` or the suite's `defaults.timeout` sets it.",
    "awx.test_parallel": "Default `test run --parallel`.",
    "ansible.index_path": "SQLite cache of refreshed source data.",
    "ansible.stale_after_seconds": "Seconds after which `source status` reports a source as "
    "`stale`.",
    "ansible.default_source": "Saved source `deps`, `impact`, `find` and `graph` use when "
    "no `--source` or inline selector is given.",
    "ansible.ref_scan_default": "Refs a source scans: `all` refs or each repo's default branch.",
    "ansible.source_refresh_backend": "Ref probe backend for source refresh.",
    "ansible.cache_dir": "Git clone cache used by source refresh.",
    "ansible.git_clone_protocol": "Protocol for source refresh clones.",
    "ansible.git_fetch_depth": "Git fetch depth for source refresh; `0` is full history.",
    "ansible.git_fetch_parallel": "Default `--parallel` for `source refresh` and `--refresh`.",
    "ansible.probe_parallel": "Concurrent ref probes during source refresh.",
    "ansible.source_refresh_repo_batch_size": "Repos committed per source refresh batch.",
    "ansible.source_refresh_rate_limit_floor": "Stop a refresh (resumable) when the GraphQL "
    "budget drops below this.",
    "ansible.git_blob_filter": "Fetch with a blob filter to download less.",
    "ansible.dependency_paths": "Dependency files scanned in each repo.",
    "ansible.sources": "Saved sources. Managed by `ansible source` commands.",
    "ansible.aliases": "Role or Galaxy name to `owner/repo` aliases. Managed by "
    "`ansible source-alias` commands.",
    "dotfiles.repos_dir": "Where `dotfiles subscribe URL` clones repos (`<repos_dir>/NAME`).",
    "dotfiles.kept_dir": "Where `apply`, `sync` and `remove` keep local files they replace, "
    "under `<repo>/<item>/<timestamp>/`.",
    "dotfiles.state_dir": "Holds `status.json`, `attention` and the advisory lock.",
    "dotfiles.tags": "This machine's tags, matched against `only` and `unless` in manifests.",
    "dotfiles.os": "This machine's OS for `os` filters; detected when unset.",
    "git.store_dir": "Root of the repo store: one shared bare repository per remote, "
    "under `<store_dir>/<host>/<path>.git`.",
    "git.untaped_helper_first": "In untaped's worktrees, ask only untaped for a host's "
    "credentials, instead of your own Git credential helpers first.",
    "dotfiles.repos": "Subscribed repos. Managed by `dotfiles subscribe`/`unsubscribe`.",
    "dotfiles.items": "Enabled items with their policy and skips. Managed by `dotfiles enable`/"
    "`disable`.",
    "dotfiles.applied": "One record per path the tool placed. Managed by `dotfiles apply`, "
    "`sync` and `remove`.",
    "recipe.library_dir": "Directory holding installed recipe packs.",
    "recipe.hook_timeout_seconds": "Per-hook request timeout; `0` disables it.",
    "recipe.hook_startup_timeout_seconds": "Timeout for preparing a hook environment.",
    "recipe.backup_keep": "`backups prune` keeps this many newest bundles by default.",
    "recipe.backup_max_age_days": "`backups prune` deletes bundles older than this by default.",
    "recipe.preview_max_rows": "Preview rows before `apply` collapses per-file rows; `0` is "
    "unlimited.",
}

_HEADER = """\
<!-- Generated by scripts/gen_config_reference.py. Do not edit by hand. -->

# Configuration reference

Every setting `untaped` reads, generated from the settings models. Profile
settings live under `profiles.<name>.<section>` in `~/.untaped/config.yml`;
state lives in `~/.untaped/state.yml` and is written only by the owning
plugin's commands. See [Configuration](../configuration.md) for the file
layout, profiles and precedence.

Set a profile setting with `untaped config set KEY VALUE` (a token:
`untaped auth set SECTION`; how values are parsed is in
[Settings](../configuration.md#settings)). Each profile setting can be
overridden for one process with the environment variable shown.
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
    """The leaf type, ``X`` for ``X | None``."""
    default: Any
    description: str | None
    optional: bool = False
    """Whether the setting is ``X | None``."""
    mark: Stability | None = None
    """The setting's own mark, else its plugin's."""


def _leaf_rows(model: type[BaseModel], prefix: tuple[str, ...]) -> list[Row]:
    """One :class:`Row` per leaf field (collections included), recursing into nested models."""
    return [
        Row(
            d.key,
            d.annotation,
            d.default,
            d.description or DESCRIPTIONS.get(d.key),
            optional=d.optional,
        )
        for d in walk_settings(model, prefix, include_collections=True)
    ]


def _type_name(row: Row) -> str:
    inner, optional = row.annotation, row.optional
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
    from untaped.settings import env_var_name  # noqa: PLC0415

    return env_var_name(key.split("."))


def collect_sections() -> list[tuple[str, str, type[BaseModel], bool, Stability | None]]:
    """``(title, prefix, model, is_state, stability)``: the shell and each first-party plugin.

    ``stability`` is the owning plugin's mark. Plugins follow in name
    order. Raises :class:`RuntimeError` naming every quarantined first-party
    plugin rather than drop its section.
    """
    from untaped.bootstrap import SHELL_SPEC  # noqa: PLC0415
    from untaped.plugins.registry import compose, discover_candidates  # noqa: PLC0415
    from untaped.settings import Settings  # noqa: PLC0415

    sections: list[tuple[str, str, type[BaseModel], bool, Stability | None]] = [
        ("Root", "", Settings, False, None),
        (
            f"`{SHELL_SPEC.section}`",
            SHELL_SPEC.section,
            SHELL_SPEC.settings,
            False,
            None,
        ),
    ]
    own = packages(REPO_ROOT)
    first_party = [c for c in discover_candidates() if canonicalize_name(c.distribution) in own]
    result = compose(SHELL_SPEC, first_party)
    if result.quarantine:
        reasons = "; ".join(
            f"{record.name!r} [{record.reason}]: {record.detail}" for record in result.quarantine
        )
        raise RuntimeError(
            f"first-party plugins quarantined; fix them before generating: {reasons}"
        )
    for registered in result.plugins:
        spec = registered.spec
        if spec.settings is not None:
            sections.append((f"`{spec.name}`", spec.name, spec.settings, False, spec.stability))
        if spec.state is not None:
            sections.append(
                (
                    f"`{spec.name}` state",
                    spec.name,
                    spec.state,
                    True,
                    None,
                )
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
    for _, prefix, model, _, stability in collect_sections():
        rows.extend(_section_rows(prefix, model, stability))
    return rows


def _section_rows(
    prefix: str, model: type[BaseModel], stability: Stability | None = None
) -> list[Row]:
    from untaped.settings import model_sections  # noqa: PLC0415
    from untaped.stability import setting_mark  # noqa: PLC0415

    rows = _leaf_rows(model, (prefix,) if prefix else ())
    if not prefix:
        # Root model: keep the shell's own settings, not the pydantic-settings base.
        rows = [row for row in rows if row.key.split(".")[0] in {"http", "ui", "skills"}]
    sections = {prefix: model} if prefix else model_sections(model)
    return [
        replace(
            row,
            mark=setting_mark(
                row.key,
                sections=sections,
                section_stability={name: stability for name in sections},
            ),
        )
        for row in rows
    ]


def _description(row: Row) -> str:
    """The description cell: the text, then the sentence for a marked setting."""
    from untaped.messages import EXPERIMENTAL_LINE, deprecated_line  # noqa: PLC0415
    from untaped.stability import Deprecated, replacement_text  # noqa: PLC0415

    if row.mark is None:
        return str(row.description)
    if isinstance(row.mark, Deprecated):
        line = deprecated_line(replacement_text(row.mark, None))
    else:
        line = EXPERIMENTAL_LINE
    return f"{row.description} {line}"


def _renamed_table() -> list[str]:
    """The Renamed settings table: every declared rename, in section order."""
    from untaped.deprecated_keys import key_mappings  # noqa: PLC0415
    from untaped.settings import model_sections  # noqa: PLC0415

    rows: list[str] = []
    for _, prefix, model, is_state, _ in collect_sections():
        if is_state:
            continue
        sections = {prefix: model} if prefix else model_sections(model)
        for section, section_model in sections.items():
            mappings = key_mappings(section_model)
            for old, new in sorted(mappings.migratable.items()):
                kind = "retired" if old in mappings.retired else "renamed"
                key = f"{section}.{old}"
                rows.append(f"| `{key}` | `{_env_name(key)}` | `{section}.{new}` | {kind} |")
            for old, deleted in sorted(mappings.deleted.items()):
                key = f"{section}.{old}"
                reason = deleted.reason()
                rows.append(f"| `{key}` | `{_env_name(key)}` | none | deleted: {reason} |")
    if not rows:
        return []
    return [
        "## Renamed settings\n",
        "A renamed key is still read with a warning; a retired one is no longer read; a "
        "deleted one is no longer read and `config migrate` removes it. See "
        "[Renamed settings](../configuration.md#renamed-settings).\n",
        "| Old key | Old environment variable | New key | Status |\n|---|---|---|---|",
        *rows,
        "",
    ]


def render() -> str:
    """Return the full Markdown page."""
    parts = [_HEADER]
    for title, prefix, model, is_state, stability in collect_sections():
        rows = _section_rows(prefix, model, stability)
        if not rows:
            continue
        parts.append(f"## {title}\n")
        if is_state:
            parts.append("| Key | Type | Description |\n|---|---|---|")
            for row in rows:
                parts.append(f"| `{row.key}` | {_type_name(row)} | {_description(row)} |")
        else:
            parts.append(
                "| Key | Type | Default | Environment | Description |\n|---|---|---|---|---|"
            )
            for row in rows:
                parts.append(
                    f"| `{row.key}` | {_type_name(row)} | {_default_text(row.default)} "
                    f"| `{_env_name(row.key)}` | {_description(row)} |"
                )
        parts.append("")
    parts.extend(_renamed_table())
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

---
name: untaped-ansible
description: Use the built-in `untaped ansible` capability for Ansible analysis.
---

# Untaped Ansible

Use this skill when the user wants an agent to operate the `untaped ansible` CLI for Ansible dependency graphing and impact analysis.

## Setup

- The command is `untaped ansible`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.ansible`; aliases and sources are `ansible` state in `~/.untaped/state.yml`.
- `untaped ansible` analyzes Ansible project roots and roles. Collections in requirements files are not traversed; `source refresh`, `graph --refresh`, and local graph targets print one warning line listing the ignored collections (live GitHub reads do not).
- GitHub API access belongs to `untaped github`; do not duplicate GitHub client behavior inside Ansible workflows.

## Command Patterns

- `untaped ansible graph` is the main command for downstream, upstream, and combined dependency views.
- Saved sources are selected with repeatable `--source NAME`; repeated sources are additive.
- Inline selectors such as `--org`, `--team`, `--repo`, `--path`, `--ref-kind`, `--ref-pattern`, and `--ref-scan-default` are also additive where accepted.
- Source-backed graphing is cache-first: it reads completed SQLite source data by default and touches GitHub only when `--refresh` is passed or `source refresh` is run. If no completed baseline exists, graph fails with the exact refresh command instead of rendering partial upstream output.
- Source refresh ref probing is backend-selectable. The default is `ansible.source_refresh_backend: auto`; use `source refresh NAME --backend auto|graphql|git` or `graph --refresh --backend auto|graphql|git` for a per-run override. `graph --backend ...` without `--refresh` is a usage error. Backend choice is not persisted on sources.
- `--cached` reads the SQLite cache as-is. That is already the default for source-backed graphs; the flag only makes it explicit.
- `--live` is the explicit opt-in for live GitHub downstream reads when a source is selected; it works even before the source's first refresh (with `--both`, upstream is then omitted with a warning). Without any source, downstream reads are always live, for remote and local targets alike; a local target with no GitHub token stays offline and warns that transitive dependencies were not expanded.
- Live reads that fail for one repo (deleted repo or tag, lost access) become graph warnings and leave that node unexpanded; auth (401) and rate-limit (429, or 403 with a rate-limit message) failures still abort.
- A local path target resolves its repo from the Git remote only when the path is the top level of a checkout (linked worktrees included); for a subdirectory such as `./roles/web` in a monorepo, pass `--target-repo OWNER/NAME`.
- `--refresh`, `--cached`, and `--live` are mutually exclusive, as are `--upstream`, `--downstream`, and `--both`; conflicting flags are usage errors (exit 2). `--refresh` also requires `--source` or inline source boundary selectors (`--org`, `--team`, or `--repo`); modifiers such as `--path`, `--ref-kind`, `--ref-pattern`, and `--ref-scan-default` do not count by themselves.
- `--team` accepts ORG/SLUG; a bare SLUG is allowed when exactly one `--org` is given and normalizes to ORG/SLUG.
- Repeating the identical graph command with inline source selectors reuses the cached scan.
- `ansible.freshness_ttl` is deprecated and ignored; commands print a warning when it is set and `untaped doctor` reports it as a `warn` row. Use `--refresh` or `source refresh NAME` when remote data should be checked.
- `--ref R --upstream` also includes consumers that declare the target without a version when `R` is the target's cached default branch; if that default branch is unknown, the graph warns how many unpinned dependents were omitted.
- Repo ids match case-insensitively (`Acme/Base` equals `acme/base`); graph node ids use the lowercase form while labels keep display casing. URLs on the GitHub host derived from `github.base_url` (GitHub Enterprise) resolve like `github.com` URLs; path-like sources such as `./local` stay unresolved.
- Tree output prints a shared subtree once and marks later occurrences `(see above)`.
- Aliases are applied when sources are refreshed; after `alias set`/`alias remove`, run `untaped ansible source refresh NAME` for cached graphs to pick up the change.
- Saved sources are managed with `source set NAME` (create or replace), `source patch NAME --add-*/--remove-*/--clear-*` (edit in place), `source get NAME`, `source list`, and `source remove NAME`. `alias set NAME OWNER/REPO` creates or replaces an alias.
- `alias set`, `alias remove`, `source set`, `source patch`, and `source remove` accept `--format`/`--columns` and print one outcome record on stdout (`ansible.alias_outcome` with `action`, `alias`, `repo`; `ansible.source_outcome` with `action`, `name`, `changes`). `action` is `created`, `updated`, `unchanged`, `deleted`, or `planned` (`--dry-run`).
- `alias remove` and `source remove` ask for confirmation; pass `--yes` when not interactive (otherwise they exit 2), or `--dry-run` to preview. Declining exits 1 with `cancelled; no changes made`.
- Row-style commands (`alias list`, `source list`, `source get`, `source status`) accept `--format pipe` for typed NDJSON: `untaped ansible source list --format pipe` emits one `{"untaped":"1","kind":"ansible.source","record":{...}}` line per row (kinds: `ansible.alias`, `ansible.source`, `ansible.source_status`). `graph` does not support `--format pipe`.
- Graph JSON includes stable `edges[].id` values and `cycles`. An edge ID is the same for every declaration of one dependency (relation, source and target), so duplicate declarations collapse to one edge. Cycle records use `kind`, `relation`, `node_ids`, and `edge_ids`: `kind="cycle"` is a closed ordered path, while `kind="scc_group"` is a sorted open SCC node set with sorted internal edge IDs when the component has too many elementary cycles to enumerate. Cycles are detected only inside the emitted depth-bounded graph, so increase `--depth` or use `--depth unlimited` when looking for longer loops.
- Malformed, templated, or wrong-shape dependency files are skipped with warnings instead of becoming repo failures. Empty files and missing/null/empty dependency sections remain warning-free; present non-list `dependencies`, `roles`, or `collections` sections warn and are skipped. Local/live graph reads surface parse skips as graph warnings; live warnings include `repo@ref path`, and `source refresh` prints skipped files to stderr for that run without persisting them into cached graph output.
- `source status` rows report `state` as `fresh`, `stale`, or `not_refreshed` and `scanned_at` as UTC (`2026-01-02T03:04:05Z`).
- `source get` is a single entity: under `--format table` it renders a vertical key:value detail view, and under `--format json` it emits a bare object (`{…}`, not a one-element `[{…}]`). The collection commands (`source list`/`status`, `alias list`) still render tables and JSON arrays.

## Agent Guidance

- Prefer JSON for machine reasoning, tree output for human impact reports, and Mermaid only when the user wants a diagram.
- Do not collapse refs. A dependency at `repo@v1` is distinct from `repo@main`.
- Treat graph cycle reports as depth-bounded evidence, not proof that no longer cycle exists outside the emitted traversal horizon.
- Upstream impact requires refreshed source data; if unavailable, prompt the user to refresh or configure sources.
- In `auto` mode, primary GraphQL rate-limit exhaustion falls back to Git `ls-remote` for the whole active probe target set. Secondary rate limiting, auth, request-level forbidden, and unknown global GitHub GraphQL access failures still abort `source refresh` and source-backed `graph` with one error. Do not treat these as per-repo failures or proceed with stale graph output.
- `source refresh` expands orgs/teams/repos with the `github` profile settings. The `git` backend still needs GitHub credentials for private sources; it only replaces the ref probe transport.
- `source refresh` is resilient to per-repo failures: successes are saved, each `failed <repo>: <reason>` is listed on stderr, and the command exits 1 with `refresh completed with N repo failures; successes were saved`. Treat that exit as partial success, not a hard failure. If every repo failed, the completed baseline is left unchanged and the command exits 1 with `refresh failed for all N repos; index left unchanged`. In explicit `graphql` mode, transient GraphQL ref-probe failures print a hint that re-running is safe and cheap. In `auto`, transient GraphQL per-repo failures and residual chunk failures first fall back to Git `ls-remote`; unrecovered repos remain failures. Graph refreshes warn instead and proceed with possibly stale data for failed repos.
- When auto fallback activates, the CLI prints a stderr warning with the repo count and reason. Large primary-rate-limit fallbacks can be much slower because Git probing runs one network subprocess per repo.
- Large `source refresh` runs are resumable. If the GraphQL budget drops below `ansible.source_refresh_rate_limit_floor` (default `500`) while repos remain, successful repo batches are committed, untouched repos/refs are preserved, the source-wide completed timestamp is not updated, and the command exits 1 with a resume hint. Re-run the same `source refresh` command to skip successful repos and retry failed or unprocessed repos; per-repo failures still make it exit non-zero.
- Tune large refreshes with `ansible.source_refresh_repo_batch_size` (default `100`) and `ansible.source_refresh_rate_limit_floor` (default `500`).
- Refresh progress and status print to stderr only; stdout stays machine-readable.
- The SQLite index is a cache. An index from an older untaped is rebuilt empty automatically with a warning; run `untaped ansible source refresh NAME` for each saved source afterwards. An index written by a newer untaped is rejected with an error naming the file, so a downgrade never destroys it.

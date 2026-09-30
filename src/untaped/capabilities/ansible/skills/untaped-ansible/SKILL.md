---
name: untaped-ansible
description: Use the `untaped ansible` command to map Ansible role and project dependencies across GitHub repositories (what a role depends on, what depends on it, which projects reach a repo, and dependency graphs). Use when the user mentions Ansible roles, requirements.yml, meta/main.yml dependencies, impact or blast-radius analysis, who uses a role, or a dependency graph.
---

# Untaped Ansible

Use this skill when the user wants an agent to operate the `untaped ansible` CLI for Ansible dependency graphing and impact analysis.

Details that do not fit here ship next to this file:

| File | Read it when |
|---|---|
| [references/sources.md](references/sources.md) | managing saved sources, source aliases, refresh backends, partial or resumable refreshes, the SQLite cache |
| [references/graphs.md](references/graphs.md) | ref and repo resolution, unpinned dependencies, local targets, tree and JSON graph output, skipped dependency files |

## Setup

- The command is `untaped ansible`. It ships with the unified `untaped` CLI (no separate install).
- Settings live under `profiles.<name>.ansible`; source aliases and sources are `ansible` state in `~/.untaped/state.yml`.
- `untaped ansible` analyzes Ansible project roots and roles. Collections in requirements files are not traversed; `source refresh`, `--refresh` on the graph commands, and local targets print one warning line listing the ignored collections (live GitHub reads do not).
- GitHub API access belongs to `untaped github`; do not duplicate GitHub client behavior inside Ansible workflows.

## Command Patterns

- Pick the command by question: `deps ROLE` (what ROLE depends on, downstream), `impact ROLE` (what depends on ROLE, upstream; needs a source), `find REPO --root ROOT` or `--stdin` (which roots contain REPO downstream). They print rows (`--format table|json|yaml|pipe|raw`, `--columns`) and default to `--depth unlimited`. `graph TARGET` renders the whole graph as `tree` (default), `mermaid` or `json` (`--out FILE`), both directions by default (`--direction up|down|both`), also `--depth unlimited` by default.
- `deps` rows (`ansible.dependency`) and `impact` rows (`ansible.dependent`) carry `repo`, `ref`, `unresolved` (declared name of a dependency with no GitHub repo), `declared_ref`/`declared_in` (verbatim, from the edge that reached it), `depth`, the shortest `path` in dependency order (from ROLE for `deps`, towards ROLE for `impact`), `root_ref` and `stopped`; one row per repo per root. Without `--ref`, what ROLE depends on is read at its default branch (`--all-refs` on `deps`/`find`/`graph` reads every cached ref, so it needs a source and exits 2 with `--live` or `--ref`, and leaves local checkouts and `find` roots with a ref as they are; a default branch the source has not scanned, as in a tags-only source, reads every ref with a warning); what depends on ROLE covers every ref a dependent pins (`root_ref` tells them apart). Warnings go to stderr.
- `ansible.default_source` names the saved source `deps`, `impact`, `find` and `graph` use whenever neither `--source` nor inline selectors are given; an unknown name fails naming the setting. `impact` without any source fails; `graph` without one omits upstream with a warning. Setting it switches `deps`/`find`/`graph` downstream reads from live GitHub to the source cache (roles outside it warn "not cached" and give no rows; before the first refresh they fail with the refresh command); `--live` reads GitHub anyway.
- Saved sources are selected with repeatable `--source NAME`; repeated sources are additive.
- Inline selectors such as `--org`, `--team`, `--repo`, `--path`, `--ref-kind`, `--ref-pattern`, and `--ref-scan-default` are also additive where accepted.
- `--live` is the explicit opt-in for live GitHub downstream reads when a source is selected; it works even before the source's first refresh (with `graph --direction both`, upstream is then omitted with a warning). `impact` has no `--live`. Without any source, downstream reads are always live, for remote and local targets alike; a local target with no GitHub token stays offline and warns that transitive dependencies were not expanded.
- `--refresh`, `--cached`, and `--live` are mutually exclusive; conflicting flags are usage errors (exit 2). The pre-9.0 `graph --upstream`/`--downstream`/`--both` still work with a deprecation warning; write `--direction up|down|both`. `--refresh` also requires `--source`, inline source boundary selectors (`--org`, `--team`, or `--repo`), or `ansible.default_source`; modifiers such as `--path`, `--ref-kind`, `--ref-pattern`, and `--ref-scan-default` do not count by themselves.
- `--team` accepts ORG/SLUG; a bare SLUG is allowed when exactly one `--org` is given and normalizes to ORG/SLUG.
- Repeating the identical command with inline source selectors reuses the cached scan.
- `find REPO...` (owner/repo, URL, or source alias; several to find any) searches downstream from roots given with repeatable `--root owner/repo[@ref]` or read with `--stdin` (bare `owner/repo@ref` lines, or pipe records with `scm_url`/`repo_url`/`repo`/`full_name` and `effective_scm_ref`/`ref`, e.g. `awx job-templates list --with-scm --format pipe`). It prints one `ansible.dependency_match` row per matched node (`root_repo`, `root_ref`, `repo`, `declared_ref` verbatim, `declared_in`, shortest `path`, plus the input record's `input_kind`/`input_id`/`input_name`, `null` for `--root` and bare lines), and nothing for roots that never reach it. Every input record gets its own rows even when records share a root, so results join back to the inputs (for example to the job templates to test). Searches the full graph unless `--depth N` is given (then an empty result says `within --depth N`); honors `--source` and the refresh flags.
- Row-style commands (`deps`, `impact`, `find`, `source-alias list`, `source list`, `source get`, `source status`) accept `--format pipe` for typed NDJSON: `untaped ansible source list --format pipe` emits one `{"untaped":"1","kind":"ansible.source","record":{...}}` line per row (kinds: `ansible.dependency`, `ansible.dependent`, `ansible.dependency_match`, `ansible.source_alias`, `ansible.source`, `ansible.source_status`). `graph` has no pipe output.

## Agent Guidance

- Prefer `deps`/`impact`/`find` rows as JSON or pipe for machine reasoning, `graph` tree output for human impact reports, and Mermaid only when the user wants a diagram.
- Do not collapse refs. A dependency at `repo@v1` is distinct from `repo@main`.
- Treat graph cycle reports as depth-bounded evidence, not proof that no longer cycle exists outside the emitted traversal horizon.
- `impact` requires refreshed source data; if unavailable, prompt the user to refresh or configure sources.
- Save a source with `untaped ansible source set platform --org acme` and index it with `untaped ansible source refresh platform`; see [references/sources.md](references/sources.md) for everything else about sources.
- Exit codes: 0 success, 1 failure (including a refresh with failed repos, whose successes are saved), 2 usage error, 4 fix the environment (`ansible.*` or `github.*` settings, a rejected GitHub token, git missing), 5 temporary (a timeout, network error or rate limit, including a paused refresh; retry later), 130 interrupted. With `--format json` stderr is JSON Lines with each error's `category`, `system` and `hint`.

# Glossary

Terms as `untaped` uses them in commands, output and these docs.

- **action**: The field of an outcome record that says what happened to one
  item: `planned`, `created`, `updated`, `deleted`, `unchanged`, `skipped`,
  `failed`, `partial`, `conflict`, `cancelled`, or a capability's own
  past-tense verb (`cloned`, `applied`, `transitioned`).
- **alias (ansible)**: A mapping from an Ansible role or Galaxy name to a
  GitHub `owner/repo`, so the dependency graph can follow it.
- **backup bundle (recipe)**: The copy of files that `recipe apply` takes
  before it writes. Restore it with `recipe backup restore`.
- **capability**: One command subtree of `untaped` (`workspace`, `github`,
  `jira`, `awx`, `ansible`, `recipe`) together with its config section, state,
  agent skill and `doctor` checks. `untaped capabilities` lists them.
- **config file**: `~/.untaped/config.yml` (or `$UNTAPED_CONFIG`). Holds
  profiles and their settings. You edit it; `untaped config` and `untaped
  profile` write it.
- **corpus (github)**: The local Git copies of repositories that `github sweep`
  searches, under `github.corpus_path`. Managed with `github cache`.
- **envelope**: One `--format pipe` line: `{"untaped": "1", "kind": ...,
  "record": ...}`. See [Pipes and record kinds](./reference/pipes.md).
- **hook (recipe)**: A Python function in a recipe pack that validates or
  transforms files.
- **key**: The dotted name of a setting: `section.field`, for example
  `github.token` or `http.verify_ssl`.
- **kind**: The type name of a record, `<capability>.<noun>` (`github.repo`,
  `awx.job`). Outcome records use `<capability>.<verb>_outcome`.
- **manifest (workspace)**: The `untaped.yml` file in a workspace directory. It
  lists the workspace's repos and branches.
- **outcome record**: The row a command that changes something prints for each
  item, with an `action` field.
- **pack (recipe)**: An installable project that contains recipes and hooks.
- **predicate hit**: A check you asked for came out true (`--fail-on-match`,
  `--strict`, `--check`). The command exits 3.
- **profile**: A named set of settings under `profiles.<name>` in the config
  file. `default` is the base layer; other profiles override it field by field.
- **provider**: A Python package that adds a capability to `untaped` through
  the `untaped.capabilities` entry point. Only capability authors deal with
  providers.
- **recipe**: A YAML file of steps that `recipe apply` plans and applies to
  target directories.
- **root commands**: The commands that belong to `untaped` itself rather than
  to a capability: `config`, `profile`, `skills`, `doctor`, `capabilities`.
- **section**: The part of a profile that one capability owns, for example
  `profiles.default.awx`. `http` and `ui` are shared root sections.
- **setting**: One configurable value in a section, addressed by its key.
- **skill**: A packaged instruction set (`SKILL.md`) that teaches an AI agent
  to use a capability. Install with `untaped skills install`.
- **source (ansible)**: A saved scan boundary (orgs, teams, repos, paths, refs)
  whose dependency data `ansible source refresh` caches for upstream impact
  queries.
- **state file**: `~/.untaped/state.yml` (or `$UNTAPED_STATE`). Data a
  capability manages itself, such as registered workspaces and Ansible sources.
  Change it only through the capability's commands.
- **sweep (github)**: A content and file-presence query over the corpus:
  `github sweep`.
- **workspace**: A directory of Git clones managed together through its
  manifest, and registered by name in the state file.

## See also

- [Getting started](./getting-started.md)
- [Configuration](./configuration.md)

# untaped documentation

These pages explain concepts, workflows and contracts. For the exact options
of the version you have installed, run `untaped --help` or
`untaped COMMAND --help`; `--columns ?` lists a command's output fields.

## Get started

- [Getting started](./getting-started.md): install, store tokens, use
  profiles, run a first command in each capability, pipe commands together.
- [Configuration](./configuration.md): the config and state files, profiles,
  secrets, TLS, and `untaped doctor`.

## Guides

| Capability | Guide | What it does |
|---|---|---|
| `workspace` | [Workspaces](./workspace/usage.md) | Create and archive task workspaces of git worktrees across repos. |
| `github` | [GitHub](./github/usage.md) | Repo inventory, GitHub search, and content sweeps across many repos. |
| `jira` | [Jira](./jira/usage.md) | Search, create, update and transition Jira Data Center issues. |
| `awx` | [AWX/AAP](./awx/usage.md) | Inspect, change, launch, sync and test AWX/AAP resources. |
| `ansible` | [Ansible dependency graphs](./ansible/usage.md) | What a role depends on, and what depends on it. |
| `recipe` | [Recipes](./recipe/usage.md) | Plan, preview and apply file changes across many directories. |

- [Agent skills](./skills.md): install and update the skills that teach AI
  coding agents to use each capability.
- [AWX agent profile](../packages/untaped-awx/src/untaped_awx/skills/untaped-awx/references/agent-profile.md):
  a dedicated AWX user, token and profile for an agent that tests its changes
  with `awx test`.

## Reference

- [Configuration reference](./reference/config.md): every setting, its type,
  default and environment variable (generated).
- [Pipes and record kinds](./reference/pipes.md): the `--format pipe`
  envelope, and which command writes and reads each record kind.
- [Exit codes](./reference/exit-codes.md): what 0 to 5 and 130 mean, the
  failure categories behind them, and which commands exit 3.
- [Environment variables](./reference/environment.md): every variable
  `untaped` reads or sets.
- [Versioning and stability](./stability.md): what a release keeps
  compatible, what is experimental, and how breaking changes arrive.
- [Glossary](./glossary.md): the terms these docs use.

## Contributing

- [Building a capability provider](./plugins.md): add a capability to
  `untaped` from your own package.
- [Command and output conventions](./conventions.md): the flags, messages,
  exit codes and record shapes every command follows.
- [Skill template](./templates/SKILL.md): the starting point for a
  capability's agent skill.
- [Releasing](./release.md): the release PR, TestPyPI rehearsal and tag.
- [AGENTS.md](../AGENTS.md) and [CONTRIBUTING.md](../CONTRIBUTING.md): repo
  rules and local setup.

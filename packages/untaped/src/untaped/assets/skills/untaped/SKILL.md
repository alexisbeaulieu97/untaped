---
name: untaped
description: Installs, sets up and diagnoses the `untaped` CLI itself (profiles, service URLs and tokens, `setup plan`, `doctor`, output formats and installed agent skills). Use when the user asks to install or set up untaped, configure or switch a profile, connect AWX/AAP, GitHub or Jira, fix a failing `untaped doctor`, or install untaped's skills for an agent.
---

# untaped

untaped is one CLI that composes plugins (`awx`, `github`, `jira`,
`workspace`, `ansible`, `recipe`, …). This skill covers the shell they share:
install, profiles, setup and health. Each plugin has its own skill
(`untaped-<name>`) for its commands.

Tokens are the user's alone. You set everything else up; the user types
every secret in their own terminal, so a token never passes through you.

## Setup

- Install with `uv tool install 'untaped[all]'` (or `'untaped[awx,github]'`
  for some plugins). Re-running `uv tool install` replaces the whole
  set, so list every extra the user wants.
- Settings live in a config file as named profiles; `default` is the base and
  other profiles override it field by field. Read settings with
  `untaped config list` and `untaped config get KEY`, which mask secrets.
- `untaped plugin list` lists what this install composes.

## Commands

| When you need to | Use |
|---|---|
| see what a profile still needs, with the command for each step | `untaped setup plan --format json` |
| the same for one profile and some services, online checks included | `untaped --profile work setup plan --only awx,jira --online --format json` |
| fail a script while setup is incomplete (exit 3) | `untaped setup plan --check` |
| hand the user one command for all their token steps | `untaped --profile work setup --only awx,jira` (the user runs it) |
| set a non-secret value | `untaped --profile work config set awx.base_url https://aap.example.com` |
| create or switch profiles | `untaped profile create work`, `untaped profile use work`, `untaped profile list` |
| check health and get each failure's fix | `untaped doctor --format json`, `untaped doctor --online --format json` |
| apply every automatic fix | `untaped doctor fix --yes --format json` |
| see where tokens come from | `untaped auth status` |
| install plugin skills for an agent | `untaped skills install awx jira --target codex` |
| keep installed skills current | `untaped skills status`, `untaped skills update` |

## Workflows

### Set up a profile with the user

1. Ask which services they use and which profile to set up (the active one
   unless they say otherwise).
2. Run `untaped --profile NAME setup plan --only SERVICES --format json`.
   Each row has `step`, `state` (`done`, `todo`, `failed`, `skipped`),
   `detail`, `run` and `by`.
3. `run` is the complete argv to pass after `untaped`. A `<NAME>` token in it
   (`<URL>`, `<COMMAND>`) is a value to ask the user for; substitute it and
   run the argv exactly as given. Run the `by: agent` rows that are `todo` or `failed`,
   in order. The `profile` row comes first, since the other rows name the
   profile.
4. Collect the `by: user` rows. When there are several, ask the user to run
   `untaped --profile NAME setup --only SERVICES` in their own terminal;
   otherwise give them the row's own command. Wait until they say it is done.
5. Re-run the plan with `--online`. Repeat until `setup plan --online
   --check` exits 0. A failed online row's `detail` says what the service
   answered and `run` is its fix.
6. Offer to install the skills of the plugins they use, by name:
   `untaped skills install awx jira --target codex` (or `claude`, or `all`).
   This skill is already installed, so `--all` would stop on it.

### Read a skill without a skills folder

`untaped skills list --format json` gives each skill's `source` directory;
read `SKILL.md` there.

### Fix a failing doctor

Run `untaped doctor fix --dry-run --format json` to see the plan, then
`untaped doctor fix --yes --format json`. `--yes` is safe: no token passes
through you (`auth migrate` moves tokens inside its own process and prints
none). Tell the user a keychain unlock prompt may appear on their screen.
Pass on each `failed` or `partial` row's `detail` to the user. The
`skipped` rows are what remains to run: each `fix` is the argv to run after
`untaped`; ask the user for each `<NAME>` value and run it, except a fix
that asks for or reveals a token (`auth set`, `config set ….token
--prompt`), which is the user's to run.

## Safety

- Never ask for a token in chat, never print or echo one, never pass one on
  a command line or through `--stdin`.
- Never open the config or state file directly; use `config list` and
  `config get`. Never pass `--show-secrets`.
- Never run a `by: user` row yourself, even when it looks harmless: it
  asks for or reveals a secret.
- You may run `untaped auth migrate`: it moves plaintext tokens into the
  password store inside its own process and prints none. Tell the user a
  keychain unlock prompt may appear on their screen.

## Pitfalls

- Read stderr as well as the rows; under `--format json` it is JSON Lines.
  Pass on what the user would want to know about, with any hint, whatever
  its `level`: a deprecated setting or flag, a skipped or partial result, a
  clamped option. Leave out progress and routine lines.
- Every `run` but `profile create` starts with `--profile NAME`. Keep it:
  dropping it acts on the active profile instead.
- Without a terminal, `untaped setup` exits 2; that is expected. Use
  `setup plan` and hand `untaped setup` to the user.
- Upstream AWX needs `untaped --profile NAME config set awx.api_prefix
  /api/v2/`; AAP uses the default.
- A token from an environment variable counts as set only alongside a
  `base_url`, and it is not tied to a profile.
- An installed skill is a copy. After upgrading untaped, run
  `untaped skills update` when untaped warns that skills are out of date.

## References

Each plugin's own skill (`untaped-awx`, `untaped-github`,
`untaped-jira`, …) covers its commands and its setup quirks.

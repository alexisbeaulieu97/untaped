# capability state lives in state.yml

Decision ID: `dec_01a0d05a563c7751139b4452ccadb3b7`

`~/.untaped/config.yml` holds only user settings: `active`, `profiles`, and
per-profile capability sections addressed as `section.key`. Capability-managed
state lives in a separate `state.yml`, next to the resolved config file unless
`UNTAPED_STATE` names another path (never the config file itself). The
profiles/env-override contract of the superseded decision is unchanged; state
models stay disjoint from profile models and are never writable through
`untaped config`.

Rationale: state is machine-written and churns (registries, aliases), while
config is hand-edited, commented, and often kept in dotfiles or shared. One file
made every state write a rewrite of the user's config, mixed generated data into
versioned dotfiles, and forced settings and state writers onto one lock.

Constraints:

- Settings writes (`config`, `profile`) touch only `config.yml`; state writes
  touch only `state.yml`, both round-trip, atomic, and locked per file.
- Migration is lazy and lossless. A state section absent from `state.yml` is
  read from the top level of `config.yml` with one deprecation warning per
  run. Its first changing write locks both files (state, then config), writes
  `state.yml`, then removes the section from `config.yml`. If that removal
  fails, `state.yml` shadows the stale copy (an emptied section stays as `{}`)
  and the user is warned; `doctor` reports any leftover section.
- `active`, `profiles`, and core setting names are never state sections.

## Related decisions

- Supersedes: [profiles and capability settings are the config contract](dec_01a0820ae00572d382078acce69af419-v4-profiles-and-capability-settings-are-the-config-contract.md)

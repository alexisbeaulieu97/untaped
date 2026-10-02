# AGENTS.md

`untaped` is one CLI that composes capability packages from this uv workspace.
Read [CONTRIBUTING.md](CONTRIBUTING.md) (developer guide) and
[docs/plugins.md](docs/plugins.md) (plugin rules) before changing code.

Agent-only notes:

- Keep imports lazy on CLI startup paths (`# noqa: PLC0415` only where Ruff
  flags it).
- Run git through `untaped.git` (`run_git`, `git_toplevel`, re-exported by
  `untaped.sdk`); never write your own `subprocess` git plumbing.
- Take advisory file locks through `untaped.fs` (`file_lock`, re-exported by
  `untaped.sdk`).
- Tasks, roadmap, priorities and handoffs live in private GitHub Issues and the
  private Untaped Project owned by `untaped-private`; its `AGENTS.md` describes
  the workflow. Do not copy private task bodies or planning exports into this
  public repository, and do not keep a second backlog here.
- Use Superpowers for design, implementation, and review; put public
  behavioral changes and their validation in the implementation PR.
- Existing user authorization persists only for its concrete scope. A backlog
  item alone does not authorize remote publication or unrelated work.

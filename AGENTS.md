# AGENTS.md

`untaped` is one CLI that composes capability packages from this uv workspace.
Read [CONTRIBUTING.md](CONTRIBUTING.md) (developer guide, including the
[checklist before you open a PR](CONTRIBUTING.md#before-you-open-a-pr)) and
[docs/reference/conventions.md](docs/reference/conventions.md) (plugin rules)
before changing code.

Agent-only notes:

- Tasks, roadmap and priorities live in this repository's GitHub issues. Do
  not keep a second backlog in the repository.
- A feature starts with a design (problem, approach, rejected alternatives,
  impact, test plan) that the maintainer approves; a bug starts with a
  failing test. Put public behavioral changes and their validation in the
  implementation PR, and fill in the PR template's drift review.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

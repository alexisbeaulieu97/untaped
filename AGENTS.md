# AGENTS.md

`untaped` is one CLI that composes capability packages from this uv workspace.
Read [CONTRIBUTING.md](CONTRIBUTING.md) (developer guide, including the
[pull request checklist](CONTRIBUTING.md#pull-requests)) and
[docs/reference/conventions.md](docs/reference/conventions.md) (plugin rules)
before changing code.

Agent-only notes:

- Tasks, roadmap and priorities live in this repository's GitHub issues. Do
  not keep a second backlog in the repository.
- Build a design ([Designs](CONTRIBUTING.md#designs)) by interviewing the
  maintainer in rounds: each round asks about every open decision whose
  prerequisites are settled, with your recommended answer; look facts up
  yourself. Write it up once the maintainer agrees nothing is open. After
  approval the work runs without check-ins until its PR is ready to merge;
  a decision only the maintainer can make goes back to the interview, which
  updates the design.
- Put public behavioral changes and their validation in the implementation
  PR, and fill in the PR template's drift review.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

# AGENTS.md

`untaped` is one CLI that composes capability packages from this uv workspace.
Read [CONTRIBUTING.md](CONTRIBUTING.md) (developer guide, including the
[pull request checklist](CONTRIBUTING.md#pull-requests)) and
[docs/reference/conventions.md](docs/reference/conventions.md) (plugin rules)
before changing code.

Agent-only notes:

- Tasks, roadmap and priorities live in this repository's GitHub issues. Do
  not keep a second backlog in the repository.
- A feature starts with a design interview with the maintainer: ask in
  rounds, each round every open decision whose prerequisites are settled,
  each question with your recommended answer; look facts up yourself. When
  no decision is left open, write the design (problem, approach, rejected
  alternatives, impact, test plan). It is reviewed like a PR (see
  CONTRIBUTING.md) and approved by the maintainer once the findings are
  fixed or answered. After approval the work runs without check-ins until
  its PR is ready to merge; a decision only the maintainer can make goes
  back to the interview, which updates the design. A bug starts with a
  failing test.
- Add each term the design settles to the root `GLOSSARY.md` (create it with
  the first): the term, a one- or two-sentence definition, and the words to
  avoid. A decision that is hard to reverse, surprising without context and
  a real trade-off gets a short ADR in `docs/adr/NNNN-slug.md`.
- Put public behavioral changes and their validation in the implementation
  PR, and fill in the PR template's drift review.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

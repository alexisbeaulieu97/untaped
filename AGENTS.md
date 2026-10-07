# AGENTS.md

`untaped` is one CLI that composes capability packages from this uv workspace.
Read [CONTRIBUTING.md](CONTRIBUTING.md) (developer guide, including the
[checklist before you open a PR](CONTRIBUTING.md#before-you-open-a-pr)) and
[docs/reference/conventions.md](docs/reference/conventions.md) (plugin rules)
before changing code. Nothing in the repository, its CI or its tooling may
require a particular AI harness or subscription: AI assistance stays optional
and replaceable, in harness-neutral files such as this one.

## What we care about

We share one vision for untaped, and the rest follows from it. We care about
it the way you care about something you'll live with for years, because we
will, and any of us may be its next maintainer. That care reaches the people
on both ends: users, for whom untaped should be a pleasure to use, and
maintainers, today's and tomorrow's, for whom it should be a pleasure to
understand, change and build on. Nobody enjoys hunting through the code for
every string a rename might break; we'd rather build the kind of project where
that never comes up. Working code is where care starts, not where it ends. A
bug often says something about the design, and so does a change that only fits
by being bolted on. The task is why we're in the code, not the limit of what
we notice.

## Agent-only notes

- Tasks, roadmap and priorities live in this repository's GitHub issues. Do
  not keep a second backlog in the repository.
- A feature starts with a design (problem, approach, rejected alternatives,
  impact, test plan) that the maintainer approves; a bug starts with a
  failing test. Put public behavioral changes and their validation in the
  implementation PR, and fill in the PR template's drift review.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

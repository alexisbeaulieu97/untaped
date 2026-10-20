# AGENTS.md

`untaped` is one CLI that composes plugin packages from this uv workspace.
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

Core and each plugin care only about what comes in and what goes out, and know
one another only through public interfaces, never how the other is built. A
plugin doesn't know other plugins exist unless it depends on one. Whoever owns
a capability does its wiring and offers it through a small interface. untaped
owns the CLI, so it wires commands, help and agent support from what plugins
declare (plugins still build commands with Cyclopts for now). A plugin owning
a contract wires it, and plugins filling it just provide data. When one side
has to know something only the other cares about, the boundary is in the wrong
place.

untaped is made of building blocks. A mechanic several features need, such
as caching or state, is solved once by its owner (core unless something
narrower owns it) behind a typed pydantic interface that is robust and simple
to use. Any plugin can use it, and its owner does too, never a private copy.

## Agent-only notes

- Tasks, roadmap and priorities live in this repository's GitHub issues. Do
  not keep a second backlog in the repository.
- A feature starts with a design (problem, approach, rejected alternatives,
  impact, test plan) that the maintainer approves; a bug starts with a
  failing test. Put public behavioral changes and their validation in the
  implementation PR, and fill in the PR template's drift review.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

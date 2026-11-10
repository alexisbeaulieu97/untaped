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
by being bolted on. Not every edge case is worth handling: we handle one when
its harm would spread beyond it, the way one missing dependency shouldn't break
every plugin, and otherwise let it fail alone, with its usual error. When edge
cases pile up, we settle on a convention instead. The task is why we're in the
code, not the limit of what we notice.

Everything untaped does can run unattended, so scripts and agents can drive it:
what a command asks at a terminal can come from a flag, an environment variable
or configuration, or another command does the job without asking. untaped
treats people and agents alike: what it says about a step names facts such as
that it asks for a secret or needs admin rights, never who should take it.

Core and each plugin care only about what comes in and what goes out, and know
one another only through public interfaces, never how the other is built. A
plugin doesn't know other plugins exist unless it depends on one. Whoever owns
a capability does its wiring and offers it through a small interface; everyone
else only declares what they need or provide, and uses the owner's interface
rather than doing the job itself, so installing a plugin that provides a
capability is all it takes for every plugin to use it. untaped owns the CLI: it
mounts the commands plugins build, wires help and agent support from what they
declare, and at run time never guesses what they don't. Declaring is the plugin
author's job. When one side has to know something only the other cares about,
the boundary is in the wrong place. The checks on these boundaries catch honest
mistakes; they don't wall plugins off. A plugin runs with the user's rights and
could get around any guard, so we add no guards that only look like protection.

untaped is made of building blocks. A mechanic several features need, such as
caching or state, is solved once by its owner behind a typed pydantic interface
that is robust and simple to use. Core holds only what every plugin needs; any
other shared mechanic lives in a plugin that the others depend on. Any plugin
can use a block, and its owner does too, never a private copy. For example, the
SDK provides output formats and commands pick those they support. Core provides
mechanics, not models of a plugin's domain; each plugin models its own.

## Agent-only notes

- Keep no backlog in the repository; tasks live in its GitHub issues.
- A feature starts with a design (problem, approach, rejected alternatives,
  impact, test plan) that the maintainer approves. Public behavioral changes
  ship with their validation in the implementation PR.
- Existing user authorization persists only for its concrete scope. An issue
  alone does not authorize remote publication or unrelated work.

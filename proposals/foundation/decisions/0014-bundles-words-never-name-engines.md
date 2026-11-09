# 0014. Bundles; words never name an engine

- **Status:** amended · settled before 2026-11-05 (7.3; kept by the maintainer on 2026-11-05 after it was put back in question) · the maintainer
- **Rests on:** [0002](0002-words-engines-users.md) words, engines, users; [0003](0003-extend-and-replace.md) extend and replace; [0013](0013-versioning-per-package.md) versioning
- **Amended by [0034](0034-bundles-definition.md):** bundles are now one definition plus conventions. A plugin may depend on a bundle; that is allowed but visible.
- **Reopen if:** people stop adding features through bundles. This condition replaced the earlier one, "installing words alone stays rare", when that one broke.

## Problem

What do you get when you install "untaped"? And when words are installed without an engine, who says what to install?

## Decision

- **A bundle** is a package with only dependencies (dsh's term). Placeholder layout: `untaped` (the bundle most people install) = core + CLI + config + renderers; an optional bigger bundle (e.g. `untaped-devops`) adds git, git-cli, github, ansible, awx, …
- Bundles replace the extras on core. Core stops knowing which plugins exist, so first-party plugins install exactly like third-party ones.
- **Anyone can publish a bundle** (e.g. `acme-untaped` with their own git engine).
- **Words never name or suggest an engine,** not even in metadata for error messages. The maintainer said no explicitly. Bundles and docs carry that guidance.
- **An empty slot is named as a hole:** which slot, and which plugin needs it. What to install is not named. This is a conscious architectural choice.
- **Installing without the CLI is supported:** an agent box (core + an MCP engine + config), or core embedded in a Python program, where the program is the host.
- The `untaped` command belongs to the CLI engine (its console script). Core can't print: it hands load problems to the host as data. Management commands are ordinary commands, available from any front end.

## Rejected

- **Words name a default engine as data.** The maintainer's objection:
  - a name in words is a *weak reference*. If the package is renamed or disappears, nothing catches it, and fixing it takes a words release, while words are meant to change rarely;
  - worse, a vanished name can be registered by someone else, and the words would then send installers to it (name squatting);
  - a bundle's dependencies are *hard* references: the resolver checks them when the bundle is released, and the bundle's CI tests the set together. "Making a set where everything is compatible is the bundle's job."
- **Words ship (depend on) a default engine.** It is swappable only at run time, not at install time, because packaging can't subtract. Every replacer would carry the default's commands, settings and failing doctor check, with a pick forced on them.
- **"Default yields"** (a declared default fills the slot only when it is alone). It is a separate change to [0012](0012-two-engines-refuse.md), left for later.

## How we got here

The packaging panel showed that under one-package-per-role, `untaped plugin add ansible` into the main bundle gives "nothing fills `git.repo_store`", and 7 plugins use git's words. So "installing words alone stays rare" was false. The maintainer kept the decision anyway: "stay with the architecture; convenience comes later". Bundle extras and installer guidance are optional convenience, left for later.

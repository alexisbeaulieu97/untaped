# Packaging, versioning and environments

> Draft. Package names are placeholders.

Every split into packages is a design choice. Everything could ship in one PyPI package and work the same; a split has two real effects: how releases are versioned, and what a user can install separately. We split where we foresee a reusable mechanic ([0047](../decisions/0047-architecture-vs-design.md)).

## Where packages are cut

- **Cut where words can be used apart, unless they change for the same reason** (Martin's Common Reuse and Common Closure principles). Reason by intent, never by today's usage ([0021](../decisions/0021-package-line-rule.md)).
- Different owners → different packages.
- A package that offers a slot never ships its filler.
- Grammar arrows across a cut point one way.
- **Identity = distribution = top-level import package**, one to one, named `untaped-<identity>`. The identity is the entry-point name; it owns the settings section and the namespace. Identity names say nothing about roles: the words own the bare name (`git`), because roles change over time and a package may hold several.
- Never split a distribution later inside a shared namespace package: pip and pipx upgrades delete the shared files. A plugin that later adds a slot gets a new package with its own import package.

## Versioning

- The unit is the package, with semver. Every package versions independently ([0013](../decisions/0013-versioning-per-package.md)).
- Floors only, no caps.
- A transition is a new name beside the old one plus `@deprecated(replacement=...)`, kept two majors. `@experimental` names may change in a minor.
- **Accepted exception:** a major of the notation (pydantic 3) is a major of every words and grammar package written in it ([0035](../decisions/0035-pydantic-is-the-notation.md)).

## Bundles

What you get when you install "untaped" is a bundle: a package with only dependencies ([0014](../decisions/0014-bundles-words-never-name-engines.md), [0034](../decisions/0034-bundles-definition.md)). Placeholder layout: `untaped` = kernel + the shared words and grammar + CLI + settings engine + renderers; bigger bundles (e.g. `untaped-devops`) add git, git-cli, github, ansible, awx. Anyone can publish a bundle. Installing without the CLI is supported: an MCP server for agents, or untaped embedded in a Python program.

## Environments

- untaped never keeps its own list of installed plugins. **The builder's record is the only list** (uv tool, pipx, a project's lock, an image manifest) ([0015](../decisions/0015-builder-owns-the-list.md)).
- An installer words package, with one engine per builder, edits that list by calling the builder's own tool. When no engine recognizes the environment, it states the facts.

## Language and processes

- untaped is written in Python ([0017](../decisions/0017-stay-in-python.md)).
- Plugins share one process and typed classes. Process boundaries exist only between tiers: runners, hubs, remote front ends ([0010](../decisions/0010-one-process.md)).

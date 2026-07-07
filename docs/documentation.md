# Fleet documentation standard

This page is normative for every repo in the `untaped` family — the core
`untaped` SDK plus each tool (`untaped-awx`, `untaped-ansible`,
`untaped-github`, `untaped-jira`, `untaped-workspace`, `untaped-recipe`,
`untaped-market`, `untaped-apple-health`). It exists because docs divided by
artifact and copied across surfaces produced measurable drift: the same fact
was documented four or five times, and the copies disagreed. The fix is a
single-source structure — one owning page per fact, everything else linking or
distilling. Each repo's `AGENTS.md` cites this page.

## The single-source rule

Every behavior fact has exactly **one** owning page. All other surfaces either
**link** to it (human-facing docs) or **distill** from it (packaged
`SKILL.md`, `AGENTS.md` summaries). They never originate facts.

Corollary: when behavior changes, the owning page is updated in the same
change, and every derived surface is re-derived in the same change. A doc PR
that ships new behavior without touching the owning page is incomplete.

Ownership splits two ways.

**Core-owned** — documented once in `untaped/docs/`; tools link and never
restate:

- the config file format and layout, and profiles — [`configuration.md`](./configuration.md)
- the env-var override shape (`UNTAPED_<SECTION>__<FIELD>`) — [`configuration.md`](./configuration.md)
- building a tool with the SDK — [`plugins.md`](./plugins.md)
- the `--format pipe` envelope and the single-entity-vs-collection render
  contract — [`plugins.md` §7](./plugins.md#7-piping) plus [`tool-conventions.md`](./tool-conventions.md)
- skills install mechanics — [`skills.md`](./skills.md)

**Tool-owned** — documented in the tool repo, never in core:

- domain command behavior
- the tool's settings keys
- the tool's pipe kind table
- domain gotchas
- release specifics

This matches the existing rule in
[`tool-conventions.md`](./tool-conventions.md): each tool documents its own
kind table locally; domain kind tables are never centralized in the SDK docs.

## Surfaces and their roles

Every repo ships the same set of doc surfaces, each with a fixed role.

| Surface | Role | Originates facts? |
| --- | --- | --- |
| `README.md` | thin front door | no — links `docs/` |
| `docs/` concept pages | canonical home of every behavior fact | **yes** |
| packaged `SKILL.md` | self-contained agent brief in the wheel | no — distills |
| `AGENTS.md` | invariants, rules, architecture, workflow, docs contract | only rules and workflow it owns |
| `CLAUDE.md` | pointer stub to `AGENTS.md` | no |
| `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE` | governance floor | no |
| `docs/superpowers/` | design artifacts (specs, plans) | out of scope |

### README.md

The thin front door. Canonical skeleton for tool repos, in order: **Install →
Configure → Quickstart → Documentation → Development → Security → Contributing
→ License**. The SDK repo is a library with no console script; it omits the
tool-only sections (Install, Configure, Quickstart) and keeps the rest of the
order.

- **Configure** lists the tool's **own** settings keys and links core
  [`configuration.md`](./configuration.md) for format, profiles, and env-var
  overrides. It never re-teaches them.
- **Quickstart** is a handful of representative commands, not a reference.
- **Documentation** points into `docs/`.

The command reference **never** lives in the README.

### docs/ (concept pages)

The canonical home of every behavior fact, including the full command
reference. Structured per the taxonomy below.

### Packaged SKILL.md

Agent-facing and shipped inside the wheel, so it must be **self-contained**. It
is **allowed to restate facts** — the one sanctioned duplication in the fleet —
but it is a **derived** artifact: it distills *from* the owning concept pages,
never introduces facts of its own, restates only what an operating agent needs,
and is re-derived in the same change whenever an owning page changes. This
restates the existing packaged-skills rule in
[`tool-conventions.md`](./tool-conventions.md), and aligns with it.

### AGENTS.md

Owns what only it can: invariants, hard rules, the architecture map, the dev
workflow, the release process, and the repo's **documentation contract**
(below). Behavior facts — schemas, command semantics, wire contracts — live in
concept pages; `AGENTS.md` links to them instead of copying. It is **not** a
standalone full reference.

### CLAUDE.md

A pointer stub to `AGENTS.md`, and nothing else.

### CONTRIBUTING.md, SECURITY.md, LICENSE

The governance floor. Every repo ships all three.

### docs/superpowers/

Design artifacts — specs and plans. Not user documentation, and outside the
documentation contract.

## Concept-first docs/ taxonomy

`docs/` is a **flat** directory of concept pages, named for the concepts users
think in — inputs, templating, safety, outputs — not for code artifacts. There
are **no Diátaxis-style subfolders** (`tutorials/`, `how-to/`, `reference/`,
`explanation/`). The Diátaxis lens informs a page's **internal** shape —
task-oriented sections versus reference sections — without imposing folder
structure.

Rules:

- Each fact has one home.
- Pages may merge when adjacent. The rule is one-home-per-fact, not a page
  count; a small repo may have few pages.
- When a fact could plausibly live in two pages, pick one and cross-link from
  the other.
- If a concept sits **between** two pages — for example a contract at the
  boundary of two features — that is the signal to give it an explicit home,
  not to let it fall into neither.

## Page style: example-first, scannable

Concept pages are read by humans; write them to be scanned, not studied.

- **Example-first.** A behavior section opens with a fenced example — recipe
  YAML, a command, sample output — and the prose after it states only what the
  example cannot show. Rule-first prose with the example as an afterthought is
  the anti-pattern.
- **No walls of text.** Paragraphs stay short (roughly four lines). A paragraph
  that packs several rules becomes a bullet list, one rule per bullet.
- **Tables for enumerable facts** — field lists, option sets, statuses,
  defaults.
- **Small sections with descriptive headings.** Renderers build the page TOC
  from headings; a reader should be able to jump straight to the rule they
  need. Headings are link targets — rename them only when updating every
  inbound link.
- Normative precision still wins: pinned error strings, defaults, and
  precedence rules must be stated exactly, in prose or table, even when an
  example already implies them.

This applies to `docs/` concept pages. The packaged SKILL.md is agent-facing
and token-budgeted: it stays dense, and this section does not apply to it.

## The documentation contract

Each repo's `AGENTS.md` carries a normative **Documentation contract** section
with three parts:

1. The sentence: *"Every behavior fact has exactly one owning page; README and
   SKILL.md link or distill, never originate."*
2. A **concept → owning page** table enumerating the repo's concept pages and
   what each owns.
3. The **in-change rule**: a behavior change updates its owning page and every
   derived surface in the same change.

Example filled table for a generic tool:

| Concept | Owning page | Owns |
| --- | --- | --- |
| Command reference | `docs/commands.md` | every command, its flags, and output shape |
| Configuration keys | `docs/configuration-keys.md` | the tool's own settings keys and defaults |
| Outputs & piping | `docs/outputs.md` | the tool's pipe kind table and format defaults |

The tool's `docs/configuration-keys.md` documents only the tool's own keys; it
links core [`configuration.md`](./configuration.md) for the file format,
profiles, and env-var override shape rather than restating them.

## Change record

GitHub **release notes are the change record**. There is **no per-repo
`CHANGELOG.md`**.

A breaking change documents its migration in the release notes **and** updates
the affected concept pages in the same change. Do **not** create version-pinned
doc files (e.g. `migration-X.Y.md`) that strand evergreen content: if a
migration note is worth keeping, its durable content belongs in a concept page.
See [`release.md`](./release.md) for the release workflow itself.

## Canonical shared-command documentation

The SDK injects the `config`, `profile`, and `skills` command groups into every
tool. These are **core-documented**; tool docs mention them only to name
tool-specific values (a settings key, a skill name). Tools never re-teach the
shared mechanics.

The sanctioned `skills` invocations — document these forms and no others:

- Quickstart form: `<tool> skills install --all`
- Named form: `<tool> skills install <skill-name>`
- Bare `<tool> skills install` with no names is a **usage error** — it requires
  names, `--stdin`, or `--all`.

Tool docs state only their skill **names** and link [`skills.md`](./skills.md)
for the install mechanics.

## Non-goals

Out of scope for this standard, explicitly:

- **No Diátaxis folder structure.** The lens shapes a page's sections, not the
  directory tree.
- **No per-repo CHANGELOGs.** Release notes are the change record.
- **No docs CI lint.** The contract lives in `AGENTS.md` and review discipline.
  A structural linter cannot catch the actual failure mode — content
  duplication and disagreement between copies — so a green lint would give false
  confidence. Reviewers enforce the single-source rule.

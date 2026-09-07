# Documentation standard

This page is normative for the unified `untaped` repository. The repository
ships one root application and a set of built-in capabilities; external
providers extend that application through the capability entry-point contract.
The purpose of this standard is to keep behavior facts in one place and keep
derived agent and contributor surfaces synchronized.

## Single-source rule

Every behavior fact has exactly one owning page. Other surfaces either link to
that page or distill it for a narrower audience. They do not originate a second
version of the fact.

When behavior changes, update the owning page and every derived surface in the
same change. A behavior change without its owning page is incomplete.

Core-owned facts live in `untaped/docs/`:

- config file layout, profiles, and env-var overrides —
  [configuration.md](./configuration.md)
- capability/provider authoring — [plugins.md](./plugins.md)
- pipe envelope and render contract — [plugins.md](./plugins.md#5-piping) and
  [tool-conventions.md](./tool-conventions.md)
- root skill discovery and installation — [skills.md](./skills.md)
- capability composition, validation, and provider metadata —
  [capabilities-spec.md](./capabilities-spec.md)

Capability-owned pages document domain commands, settings keys, pipe kind tables,
domain behavior, and capability-specific gotchas. They link to these core
pages for shared mechanics instead of copying them.

## Surfaces and roles

| Surface | Role | May originate behavior facts? |
| --- | --- | --- |
| `README.md` | front door and quickstart links | no |
| `docs/` concept pages | canonical behavior references | yes |
| `src/untaped/capabilities/<name>/skills/SKILL.md` | self-contained agent brief | no; it distills |
| `AGENTS.md` | invariants, architecture, workflow, and doc contract | only rules it owns |
| `CONTRIBUTING.md` | contribution and governance floor | only contribution rules |
| `docs/superpowers/` | design artifacts and plans | no user reference |

The packaged skill is allowed to restate the facts an operating agent needs,
but it remains derived from the owning concept pages. Update it when its
capability behavior or workflow changes.

## Page style

Concept pages should be example-first and scannable:

- Open a behavior section with a small command, config, or output example.
- Keep paragraphs short and use tables for enumerable fields and options.
- State pinned defaults, precedence, and error behavior precisely.
- Link to the owning page instead of duplicating shared command mechanics.

The root command reference belongs in the appropriate concept page. The README
should remain a front door rather than a second reference.

## Root command ownership

The unified shell owns these management commands:

```text
untaped config …
untaped profile …
untaped skills …
untaped doctor
untaped capabilities
```

Capability command trees use `untaped <capability> …`. Capability pages may
name their settings and skill IDs, but shared config, profile, skill, doctor,
and provider mechanics belong in the core pages above.

The sanctioned skill forms are:

```bash
untaped skills install --all
untaped skills install <short-or-full-skill-id>
untaped skills install --stdin
```

A bare `untaped skills install` is a usage error. The short selector is the
primary user-facing form; the full `SkillAsset.name` remains the stable
installed ID and is accepted for scripts and raw-list pipelines.

## Change record

Release notes are maintained through the repository's release workflow. A
breaking behavior change updates its owning concept page and the relevant
capability skill in the same change. Keep durable instructions in concept pages
rather than creating version-pinned copies of evergreen guidance.

## Review checklist

Before merging documentation with a capability or root behavior change, check
that:

1. the owning page states the new behavior and exact command/config syntax;
2. capability pages link to shared mechanics rather than restating them;
3. affected packaged skills are re-derived with stable skill IDs;
4. examples use the unified `untaped` executable and fully qualified root config
   keys; and
5. provider examples import only from `untaped.capability_api`.

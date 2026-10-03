# How composition works

`untaped` is one distribution with one executable: `untaped --version` prints
the installed `untaped` distribution's version, and a provider adds commands
under `untaped <capability>` rather than a console script of its own. At
startup the root composes the capabilities in this order:

1. **Discovery.** Every entry point in the `untaped.capabilities` group is a
   candidate, first-party ones included. Its distribution's `untaped`
   requirement is checked against the running version.
2. **Resolution.** The entry point is loaded and called, and must return a
   `CapabilitySpec`.
3. **Validation.** The declaration is checked: reserved root names, the
   entry-point name, duplicate names, sections, skills and doctor checks,
   and overlapping profile and state fields.
4. **Commit.** The survivors are mounted under their names, lazily or not
   as [the capability app](./plugins.md#settings-and-the-capability-app) describes.

Every violation quarantines that provider and composition continues: a
warning names it, `untaped capabilities` lists it as quarantined, and
`untaped doctor` shows the reason. The root owns configuration, profiles,
themes and the management commands, and aggregates every capability's skills
and doctor checks.

When two providers claim the same capability name or config section, all of
them are quarantined and a warning names every claimant: no provider can take
over another's commands or settings, and the result does not depend on install
order. Uninstall one to restore the other. A capability whose settings import
another capability's `api` is quarantined with it when that import fails.

## Depending on another capability

A capability may import another only through that capability's public
module, `<package>.api` (for example `untaped_github.api`), never its other
internals, and only when its distribution depends on the other's (a
dependency under an extra does not count). Dependencies are one-way, and
imports of another capability stay lazy on CLI paths; a settings model that
validates against the other capability may import it at module top. An `api`
module keeps a closed `__all__`. Logic two capabilities need lives in exactly
one owner's `api` module, never forked into both.

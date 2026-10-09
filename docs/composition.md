# How composition works

`untaped` is one distribution with one executable: `untaped --version` prints
the installed `untaped` distribution's version, and a provider adds commands
under `untaped <plugin>` rather than a console script of its own. At
startup the root composes the plugins in this order:

1. **Discovery.** Every entry point in the `untaped.plugins` group is a
   candidate, first-party ones included. Its distribution's `untaped`
   requirement is checked against the running version.
2. **Resolution.** The entry point is loaded and called, and must return a
   `PluginSpec`.
3. **Validation.** The declaration is checked: reserved names, the
   entry-point name, duplicate names, skills and doctor checks,
   overlapping settings and state fields, and the settings model's
   [renamed keys](./reference/conventions.md#renaming-a-setting).
4. **Commit.** The survivors are mounted under their names, lazily or not
   as [the plugin app](./plugins.md#settings-and-the-plugin-app) describes.

Every violation quarantines that provider and composition continues: a
warning names it, `untaped plugin list` lists it as quarantined, and
`untaped doctor` shows the reason. The root owns configuration, profiles,
themes and the management commands, and aggregates every plugin's skills
and doctor checks.

When two providers claim the same plugin name, all of them are quarantined
and a warning names every claimant: no provider can take over another's
commands or settings, and the result does not depend on install order. Uninstall one to restore the other. A plugin whose settings import
another plugin's `api` is quarantined with it when that import fails.

The import rules between plugins are in
[Depending on another plugin](./reference/conventions.md#depending-on-another-plugin).

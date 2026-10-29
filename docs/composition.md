# How composition works

`untaped` is one distribution with one executable: `untaped --version` prints
the installed `untaped` distribution's version, and a plugin adds commands
under `untaped <plugin>` rather than a console script of its own. At
startup the root composes the plugins in this order:

1. **Discovery.** Every entry point in the `untaped.plugins` group is a
   candidate, first-party ones included. Its distribution's `untaped`
   requirement is checked against the running version.
2. **Resolution.** The entry point is loaded, never called, and must name a
   plugin class in a leaf module (`untaped_acme.plugin:Acme`); anything
   else, an 11.x `PluginSpec` included, is quarantined (`not-a-plugin-class`).
3. **Validation.** The declaration is checked: reserved names, duplicate
   names, skills and doctor checks, and the settings model's
   [renamed keys](./reference/conventions.md#renaming-a-setting).
4. **Commit.** The survivors are mounted under their names. A plugin's
   commands are built from its declaration the first time one is
   dispatched; one whose commands can't be built fails them, `--help`
   included, with exit 4, and `untaped doctor` shows why.

A plugin class is composed once: core keeps
the composed declaration under `~/.untaped/plugins/untaped/cached/declaration/`,
one file per environment and set of installed plugins, so a later start reads
it instead of importing the class (which imports every command). A plugin's
class is imported when one of its commands runs or one of its blocks is read;
a plugin with a credential is still imported at every start. The declaration
follows untaped and every installed plugin distribution, by version and by
the files it installed (an editable install's source files too): installing,
upgrading or editing one composes again on the next start. A change to
another library that plugins import does not, so after one that changes what
a plugin declares, delete the folder; nothing else is lost. A home that can't
be written composes at every start.

Every violation quarantines that plugin and composition continues: a
warning names it, `untaped plugin list` lists it as quarantined, and
`untaped doctor` shows the reason. The root owns configuration, profiles,
themes and the management commands, and aggregates every plugin's skills
and doctor checks.

When two distributions claim the same plugin name, all of them are quarantined
and a warning names every claimant: no plugin can take over another's
commands or settings, and the result does not depend on install order.
Uninstall one to restore the other. A plugin whose settings import another
plugin's `api` is quarantined with it when that import fails.

The import rules between plugins are in
[Depending on another plugin](./reference/conventions.md#depending-on-another-plugin).

## Contracts

How a contract's providers load, are set aside and are chosen between is in
[Contracts](./contracts.md#how-providers-are-loaded-and-chosen).

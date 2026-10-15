Scripts that ran `untaped capabilities` run `untaped plugin list` and read
kind `untaped.plugin`. A third-party plugin replaces its entry point
`<name> = "untaped_<name>:provider"` in the `untaped.capabilities` group
with `<name> = "untaped_<name>:SPEC"` in `untaped.plugins`, and may delete
`provider()`. It renames `CapabilitySpec` to `PluginSpec` and
`CapabilityContext` to `PluginContext` (a doctor check reads only its
`settings`), drops `CapabilityProvider`, passes `settings=` and `state=`
instead of `profile_model=` and `state_model=`, and drops `config_section`:
one name serves as distribution `untaped-<name>`, import package
`untaped_<name>`, entry point, `PluginSpec` name, config section and
command group. A plugin whose old `config_section` differed from its name
moves that section in `config.yml` and `state.yml` and renames its
`UNTAPED_<SECTION>__*` variables. Tests that called `provider_candidate`
call `plugin_candidate`.
([#554](https://github.com/alexisbeaulieu97/untaped/pull/554),
[#588](https://github.com/alexisbeaulieu97/untaped/pull/588))

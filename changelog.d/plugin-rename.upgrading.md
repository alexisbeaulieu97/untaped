Scripts that ran `untaped capabilities` run `untaped plugin list` and read
kind `untaped.plugin`. A third-party plugin moves its entry point to the
`untaped.plugins` group and uses one name throughout: distribution
`untaped-<name>`, import package `untaped_<name>`, and `<name>` as entry
point, `PluginSpec` name, config section and command group. It passes
`settings=` and `state=` and no `config_section`; a doctor check reads only
`PluginContext.settings`. A plugin whose old `config_section` differed from
its name finds its settings under the name now: move that section in
`config.yml` and `state.yml` and rename its `UNTAPED_<SECTION>__*` variables.

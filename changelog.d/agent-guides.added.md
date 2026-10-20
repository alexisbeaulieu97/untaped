A plugin can ship guides, Markdown pages for what belongs to no single
command, with `PluginSpec(guides=files("untaped_acme") / "guides")`: its
agent page lists them and `untaped agents PLUGIN GUIDE` prints one. `lint`
checks them (`guide-name`, `guide-description`, `guide-heading`,
`guide-link`, `guide-command`, `guide-yaml`), and `map-line` caps a plugin's
lines on the map at 120 characters
([agent-pages.md](https://github.com/alexisbeaulieu97/untaped/blob/v11.1.0/docs/reference/agent-pages.md#guides)).

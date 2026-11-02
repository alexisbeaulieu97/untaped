untaped reads what each installed plugin imports from untaped and other
plugins, once per plugin version, and looks each name up before the plugin
runs. A missing name disables that plugin alone (`uses-missing`), naming
the file and line and whether the plugin is out of date or needs a newer
untaped; a deprecated one warns once and shows in `untaped doctor` as a
`plugin-uses` row, as does a deprecated contract method a provider fills.
`untaped lint` reports these as `uses-deprecated` warnings, and a star
import of untaped or another plugin as an `import-boundary` error. The
`untaped.deprecation` line gains a `plugin` field.

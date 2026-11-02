A deprecated name a plugin's code looks up or calls while a command runs
warns once per run, naming the plugin in the sentence and in the
`untaped.deprecation` line's new `plugin` field; the SDK's modules warn at
the lookup itself, an import included. `untaped doctor` lists each as a
`plugin-uses` row, as it does a deprecated contract method a provider
fills. `untaped lint` reports both, and each deprecated name a plugin
imports, as `uses-deprecated` warnings, and a star import of untaped or
another plugin as an `import-boundary` error. A plugin that fails to load
because untaped or another plugin lacks a name it uses is disabled alone
(`uses-missing`), naming the file and line, the owner's installed version,
what the plugin requires of it, and both fixes.

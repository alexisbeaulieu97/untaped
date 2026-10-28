**Breaking (plugins):** `untaped.sdk.get_config_section` and
`untaped.sdk.report_errors` are gone. A 12.0 command reads its settings as
`self.settings` and raises an `UntapedError` for core to print, so no plugin
calls either.

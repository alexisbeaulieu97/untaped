`get_config_section("acme")` becomes `self.settings` in a command, or
`settings_of(AcmeSettings)` elsewhere in the run; a test reads them inside
`with install(Plugin).running():`. Drop `with report_errors():` around a
command's body: raise the `UntapedError` and core prints it and exits with
its code.

# unified distribution and CLI version identity

Decision ID: `dec_01a0820d62a473538c658510093a134c`

Untaped has one distribution and one executable identity. `untaped --version`
reads the installed `untaped` distribution metadata and prints that exact
version followed by a newline. The release unit is the unified package, whose
wheel and source archive include the composed application and built-in
capabilities.

Release smoke checks bind package metadata, the executable, and root and
capability help together. Standalone tool distribution overrides and retired
standalone executable assumptions are outside the application contract.

## Related decisions

- Supersedes: [dec_019f68b6b8fe72f49640813dd3240a0c](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b8fe72f49640813dd3240a0c-tool-versions-come-from-installed-distribution-metadata-sdk-3-1.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820d62a473538c658510093a134c-v4-unified-distribution-and-cli-version-identity.md).

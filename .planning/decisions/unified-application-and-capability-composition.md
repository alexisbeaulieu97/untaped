# unified application and capability composition

Decision ID: `dec_01a0820ac45e77eab1dcf65e9eae55b0`

Untaped ships one `untaped` application and executable. The root owns shared
configuration, profiles, management commands, capability reporting, and root
options. Capabilities declare their own skill assets and doctor checks; the
root aggregates them for the skills and doctor commands. Built-in capabilities
mount under `untaped <capability> ...`.

External providers are discovered through the `untaped.capabilities` entry
point group and validated against `untaped.capability_api` before they mount.
External provider failures are quarantined so the root can continue; built-in
violations are fatal. Standalone per-capability executables and the retired
standalone composition helpers are not part of the application contract.

## Related decisions

- Supersedes: [dec_019f68b6b2cb75a9a7cb908963b4b59c](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b2cb75a9a7cb908963b4b59c-untaped-is-an-sdk-not-an-app-plugins-retired.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820ac45e77eab1dcf65e9eae55b0-v4-unified-application-and-capability-composition.md).

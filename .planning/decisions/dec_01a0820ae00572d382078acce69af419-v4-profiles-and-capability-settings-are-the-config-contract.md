# profiles and capability settings are the config contract

Decision ID: `dec_01a0820ae00572d382078acce69af419`

Untaped uses `~/.untaped/config.yml` with `active`, `profiles`, and
capability-managed top-level state. `http` and `ui` are ordinary per-profile
settings. `untaped config` addresses settings by fully qualified names such as
`github.token` and `http.verify_ssl`; a capability owns its section.

The default profile layers beneath the active profile, and environment
overrides use `UNTAPED_<SECTION>__<FIELD>`. Capability state models are
disjoint from profile models and remain managed by their capability. Legacy
flat-layout compatibility warnings and standalone-tool key expansion are not
part of the current application contract.

## Related decisions

- Supersedes: [dec_019f68b6b5ea758190f84ea79ed1cfbf](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b5ea758190f84ea79ed1cfbf-config-format-v1-sdk-1-x-v2-sdk-2-x.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820ae00572d382078acce69af419-v4-profiles-and-capability-settings-are-the-config-contract.md).

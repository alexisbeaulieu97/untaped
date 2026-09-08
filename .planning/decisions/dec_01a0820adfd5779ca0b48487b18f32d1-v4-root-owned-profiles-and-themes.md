# root-owned profiles and themes

Decision ID: `dec_01a0820adfd5779ca0b48487b18f32d1`

Untaped keeps profiles, themes, and shared settings under the unified root.
`untaped profile ...` manages the shared profile layout, and `untaped config
...` reads and writes fully qualified `section.key` settings. The root
`--profile` option selects an invocation profile; the default profile layers
beneath the selected profile.

Capabilities own their profile and managed-state fields. State fields are
disjoint from profile fields and are not writable through `untaped config`.
Themes are root UI settings. Standalone profile or theme command packages are
outside the application contract.

## Related decisions

- Supersedes: [dec_019f68b6b3da73acb58a68dffdc85adc](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b3da73acb58a68dffdc85adc-profiles-and-themes-are-absorbed-into-the-sdk.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820adfd5779ca0b48487b18f32d1-v4-root-owned-profiles-and-themes.md).

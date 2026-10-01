# capability-owned packaged skills

Decision ID: `dec_01a0820d443870069dfe37af04c1fcc2`

Untaped packages agent skills with capabilities. A capability declares its
`SKILL.md` assets on `CapabilitySpec`; the unified root discovers the accepted
union and `untaped skills list` or `untaped skills install` manages it. Stable
asset IDs such as `untaped-github` remain available, while short selectors
such as `github` are convenient root-level inputs.

There is no separate core agent skill or standalone skill-management package.
Provider authoring guidance belongs in the public provider documentation, and
capability-specific usage belongs in the capability's packaged skill.

## Related decisions

- Supersedes: [dec_019f68b6b6f0748b8a01f0cbe11b7f12](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_019f68b6b6f0748b8a01f0cbe11b7f12-the-sdk-ships-no-core-agent-skill.md) (historical record)

Source: [preserved decision record](https://github.com/alexisbeaulieu97/untaped/blob/de9a55d4842c50f5ba6afb93e6715d810c27b62f/.untaped/orchestration/decisions/dec_01a0820d443870069dfe37af04c1fcc2-v4-capability-owned-packaged-skills.md).

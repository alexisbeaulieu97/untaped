`untaped skills install` writes a stub skill for a plugin that declares a
`SkillAsset` without `source`: a short `SKILL.md` from untaped's template and
the plugin's description that sends the agent to `untaped agents`, so one
stub serves every untaped on the machine. Its marker, `.untaped-stub.json`,
records the untaped and plugin versions that wrote it. untaped's own
`untaped` skill is now a stub. `skills update --force` also replaces a stub
someone edited or a newer untaped wrote.

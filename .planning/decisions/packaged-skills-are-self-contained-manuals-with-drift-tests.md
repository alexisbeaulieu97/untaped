# packaged skills are self-contained manuals with drift tests

Decision ID: `dec_01a0eb3c543973f380439c95cbdf7ec5`

A capability's packaged skill is the complete manual for an agent that has
only the installed CLI. `SKILL.md` is about 900 words: the map (when to use
what, the main loop, pitfalls). Details go in `references/*.md` and sample
inputs in `examples/`, in the same directory, linked by relative path. A skill never points
at `docs/` or the source repository. Document formats an agent writes are also
described by the installed version itself: `untaped awx schema KIND` prints
the JSON Schema generated from the models.

Rationale: agents run where the CLI is installed, not where the repository is
checked out, so a link into `docs/` is a dead end. A manual copied from the
code drifts unless something fails when it does.

Constraints:

- Tests parse every `untaped …` command quoted in any skill file, example
  comment or the `awx test init` starter against the real command tree
  (resolve and bind, never run), load every AWX example suite through the
  real loader, match the suite reference's fields against the generated
  schema both ways, reject repository paths and links outside the skill, and
  require `SkillAsset.description` to equal the `SKILL.md` frontmatter.
- The user guide in `docs/` may keep its own prose, but agent-facing content
  that exists only once (such as the AWX agent profile) lives in the skill,
  and the guide links to it.

## Related decisions

- Refines: [capability-owned packaged skills](capability-owned-packaged-skills.md)

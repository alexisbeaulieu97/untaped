# Writing a plugin's skill

A plugin's agent skill is a stub: a short `SKILL.md` that untaped writes from
its own template and one sentence of the plugin's, and that sends the agent
to `untaped docs` (see [Agent skills](./skills.md)). The plugin declares it
on `PluginSpec.skills` with a name and a description, and nothing else:

```python
SkillAsset(
    name="untaped-acme",
    description=(
        "Acme deployments through untaped: releases, rollbacks and their audit log. "
        "Use when the user wants to ship, roll back or inspect an Acme release."
    ),
)
```

`name` is the skill's full ID, `untaped-<plugin>`. A plugin ships at most
one skill.

## The description

The description is the only text of the plugin's in the stub, and the
agent's skill loader reads it to decide whether to load the skill, so it
routes rather than instructs:

- In the third person, it says what the plugin covers, then the intents that
  should load it.
- It stays on this plugin's ground, and says where the plugin stops when a
  nearby task needs no untaped (workspace's: "A repo outside a workspace
  needs only plain git.").
- It names no command or option: those depend on the installed version, and
  one stub serves every untaped on the machine.
- Aim for under 60 words. Composition requires only that it isn't empty.

## Where everything else goes

What the agent needs beyond the description it reads on the plugin's
pages, which untaped generates from what the plugin already declares:
docstrings, `Examples`, option help, setting and record field
descriptions, and [guides](./reference/docs.md#guides) for what
belongs to no single command. [How a plugin shapes its
pages](./reference/docs.md#how-a-plugin-shapes-its-pages) lists
each.

Write facts, not advice: what a command does, what it changes, how it
fails. Which steps to confirm with the user is the agent's call.

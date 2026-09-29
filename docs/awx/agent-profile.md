# AWX agent profile

An AI agent that tests its own changes with [`untaped awx test`](./usage.md#test-suites)
should run as its own AWX user with its own token, with only the roles its
suites need, so a mistaken command fails with a permission error instead of
changing something it should not.

The setup guide (the AWX user, its roles and token, the untaped profile, and
how the agent runs suites) ships with the CLI in the `untaped-awx` skill, so
the agent reads the same page:
[agent-profile.md](../../src/untaped/capabilities/awx/skills/untaped-awx/references/agent-profile.md).
Install the skill for your agent with:

```bash
untaped skills install awx --target claude
```

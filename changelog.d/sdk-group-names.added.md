The SDK's groups list their names: `untaped.sdk.storage`, `.network`,
`.health`, `.git`, `.projects` and `.prompt` export the blocks and helpers
each is home to (`from untaped.sdk.storage import Cache`), loading a
member only when it is first used. The root still exports the same objects.

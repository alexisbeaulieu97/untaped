`untaped.sdk.read_stdin_text()` reads the text piped to a command, for a
`PathOrStdin` given `-`; `PathOrStdin` also marks list items
(`Positional[list[PathOrStdin]]`). A `-` is refused (exit 2) from a
terminal, and beside `--stdin`, before the command runs; a `TextOrStdin`
given `-` is the piped text.

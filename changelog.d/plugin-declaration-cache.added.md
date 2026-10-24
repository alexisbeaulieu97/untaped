Core keeps the composed declaration of the 12.0 plugin classes under
`~/.untaped/plugins/untaped/cached/declaration/`, so a start after the first
reads it instead of importing each plugin class and its commands. Any change
to installed code composes again.

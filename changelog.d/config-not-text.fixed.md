A `config.yml` that isn't UTF-8 text, or is a directory, stops every command
with a clear error (exit 4) instead of a traceback; a directory no longer
reads as an empty config.

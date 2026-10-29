**Breaking (plugins):** a line a plugin emits from a thread it started
itself, without copying its context, no longer reaches the newest open run:
it goes to the default text sink, ignores `--format`, `-q` and `-v`, and
never counts toward the exit code. `bounded_map` and `Parallel.map` copy the
context, as every thread untaped starts now does.

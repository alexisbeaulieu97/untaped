A plugin that starts its own `threading.Thread` or `ThreadPoolExecutor`
starts its workers with `bounded_map` or `Parallel.map`, or runs each target
in `contextvars.copy_context().run`.

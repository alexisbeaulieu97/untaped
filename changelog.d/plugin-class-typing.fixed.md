A plugin class passes as `type[Plugin]` to a type checker (`install(MyPlugin)`
without a cast), and a test's providers plugin (`install(..., providers=)`) is
listed under the test's own package instead of core's.

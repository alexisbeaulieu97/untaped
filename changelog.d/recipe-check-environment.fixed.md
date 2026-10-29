`untaped recipe validate` and `recipe test` stop with the environment's error
(exit 4 when `uv` is missing) instead of reporting every hook pack as failed
(exit 1).

**Breaking (awx):** A batch that writes or launches several resources
(`apply`, `patch`, `edit`, `delete`, `launch`, `sync`) now keeps going after a
failure and reports every row, as `workspace run` and the core batch helpers
do. `--fail-fast` stops scheduling after the first failure and skips the rest;
`--continue-on-error` is gone.

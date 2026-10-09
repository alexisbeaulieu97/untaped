awx: `launch` (job and workflow templates) and `sync` (projects, inventories
and inventory sources) report a job that failed, timed out or was skipped as a
`warning` line naming the target, and a failed request as an attributed
`error` line, so JSON diagnostics no longer label them `info`.
([#488](https://github.com/alexisbeaulieu97/untaped/pull/488))

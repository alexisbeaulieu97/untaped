`awx jobs logs --tail` rejects a negative number as a usage error instead of
treating it like `--tail 0`.

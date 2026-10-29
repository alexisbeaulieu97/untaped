`untaped lint --select` (and `awx lint --select`) naming no installed rule is
refused (exit 2); it only warned and passed with nothing reported, so a CI
step pinning a misspelled rule never failed.

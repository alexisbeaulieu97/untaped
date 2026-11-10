`awx export --out-dir` names files with letters, digits and `._-` only (any
other character becomes `_`), and a name over 128 bytes ends in a hash, so a
long resource name no longer stops the export; files written before still
apply.

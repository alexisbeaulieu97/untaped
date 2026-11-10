`untaped auth login --store NAME` with a store this machine can't use exits 4
(the environment needs fixing) instead of 5 (retry later), as it already did
with no usable store at all.

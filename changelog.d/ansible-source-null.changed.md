`ansible source list` and `ansible source get` now write
`ref_scan_default: null` for a source that does not set it, instead of
leaving the field out.

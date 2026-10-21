A recipe backup's `metadata.json` is now written in untaped's stamped data
file format, readable by its owner only, with its fields where 11.0 reads
them: backups 11.0 made still list, show and restore, and 11.0 still reads
new ones. Metadata a newer untaped wrote is reported as such instead of
being read as damaged.

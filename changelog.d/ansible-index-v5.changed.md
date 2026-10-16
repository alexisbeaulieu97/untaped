The ansible index (schema 5) also stores the variables each indexed repo's
YAML files and templates define, set and read. The index is upgraded in
place, and the first refresh after the upgrade analyses each repo's files
once; later refreshes analyse only files that changed. The index now follows
the version of the variable analysis and of the dependency parser, so an
untaped upgrade that changes either refreshes each saved source once.

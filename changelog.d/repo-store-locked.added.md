`RepoStore.locked()` (experimental) holds a repo store repo's lock across
several calls, such as a read, a fetch and a write, so they act as one step.

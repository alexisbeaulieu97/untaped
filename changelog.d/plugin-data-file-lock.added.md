`DataFile[Mark].at(path)` reads and writes one stamped file wherever its
owner places it: `read()` answers found, absent, unreadable or a newer
untaped's, and `write()` takes the file's lock, keeps the keys the model
doesn't know and never overwrites a newer untaped's file. A 12.0 plugin
class declares an operation lock as `sync = Lock(wait=timedelta(minutes=10))`:
`locked()` waits that long for another process, `try_locked()` tries once,
and taking it again in one thread only counts.

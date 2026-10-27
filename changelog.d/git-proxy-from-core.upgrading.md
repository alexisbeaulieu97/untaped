A plugin filling `GitHost` drops its `proxy` method; untaped's proxy covers
its host's HTTPS fetches. Set `untaped.http.proxy` only where every HTTPS
Git host should go through it, or leave it unset and let Git's own config
choose per host.

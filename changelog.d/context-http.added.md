Commands and providers build their HTTP clients with
`self.ctx.http.client(self.settings, base_url=..., bearer=...)`, which
also takes `verify=` and `headers=` (`untaped.sdk.network.HttpFactory`):
core adds the proxy, trust, timeout and retries, binds the bearer to its
origin and attributes failures to the plugin whose settings it is given.
A client class takes the factory (`JiraClient(settings, http=self.ctx.http)`);
its tests take the testing kit's (`with install(Jira).running() as run:`,
`run.http`).

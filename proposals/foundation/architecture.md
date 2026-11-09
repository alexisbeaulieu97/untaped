# Architecture: the kernel

> Draft. Settled pieces only. The kernel's stress test is under way: concern 1 holds ([0054](decisions/0054-recording-is-atomic.md)); concerns 2 (a plugin misbehaving inside a hook becomes a fact) and 3 (leaving a scope restores the previous state) are pending. Names in code are placeholders.

The architecture is the kernel and nothing else. Everything on this page is there because without it plugins couldn't find or call each other. It is mandatory for every plugin, and adding to it is a deliberate decision. Everything else untaped does is design, and can be swapped ([0047](decisions/0047-architecture-vs-design.md)).

The kernel ships as `untaped-core`. It uses the standard library only.

## The founding principle

The kernel is a protocol: **a courier protocol built in Python**. It doesn't know where it runs or what it has access to. Its only opinions are its shapes. It **routes by types**: contracts, slots and their fillers are typed objects, never names. ([0053](decisions/0053-founding-principle.md))

Its job is to keep a **valid state at all times**:

- every operation ends in exactly one known outcome: a value, or one of the kernel's facts;
- its own records (what's loaded, what fills what, the open scopes) are never left half-changed;
- an ambiguity or an error is a valid state.

| Courier protocol | Kernel |
|---|---|
| addresses | `offers` / `fills` |
| the package | `Call` |
| the receipt | `Reply`, holding a `Result` |
| the courier | `Dispatch` |
| which address, if several | `Resolver` |
| a delivery round | `scope` |
| returned to sender | the facts (`Unfilled`, `Undecided`, `Ambiguous`, `Loop`, `NoScope`, `Err`, `LoadProblem`) |

Every rule below follows from this principle. Each section says which part it serves.

## What the kernel is not

- It holds no engine, no vocabulary and no data conversion. It reads no files and no environment. It never imports a words or grammar package. ([0020](decisions/0020-core-is-the-kernel-only.md), [0026](decisions/0026-kernel-stripped.md))
- It never enforces conventions. The buckets, the notation and the grammar rules are design; they get teeth only from plugins that own them and from our repository's tests. ([0047](decisions/0047-architecture-vs-design.md))
- It never decides between competing words, between engines, or between providers. ([0004](decisions/0004-words-without-a-center.md), [0029](decisions/0029-resolvers.md))
- It never claims a plugin works. It records addresses and carries calls. ([0054](decisions/0054-recording-is-atomic.md))
- It has no `main`. A **host** (the CLI executable, an MCP server, a hub, a program embedding untaped, a test) calls into it: load, open a scope, ask, show the results. ([0041](decisions/0041-process-slot-and-host.md))

## What a plugin must declare

Only what the kernel needs in order to route: what a plugin **offers** (it owns these contracts and slots) and what it **fills** (it answers or implements these). Anything else a plugin wants others to declare, such as `uses`, belongs to that plugin, not to the kernel. ([0048](decisions/0048-kernel-requirement-rule.md))

```python
class GitHub(Plugin):
    offers = ()
    fills = (GitHubRepos,)          # GitHubRepos answers RepoSource
```

*Serves: routing by types.*

## Loading and recording

1. The kernel finds plugins through the `untaped.plugins` entry points.
2. A plugin that fails to import is reported as data, `LoadProblem`, for the host to show. The other plugins load. ([0026](decisions/0026-kernel-stripped.md))
3. **Recording is atomic.** After a plugin's import succeeds, the kernel records all its declarations in one step, or none. Nothing registers by side effect. ([0054](decisions/0054-recording-is-atomic.md))
4. Then the kernel announces the moment `Loaded`: "this plugin finished loading", with its declarations. Owners check how others use their words here.

The kernel records addresses, not quality. A filler its owner dislikes is still recorded and still routed; a call to it ends in `Ok` or `Err`. Catching it earlier is the owner's choice.

*Serves: records never half-changed; every operation ends in a known outcome.*

## Two ways to plug in

- **`Contract` (extend):** the owner asks, every provider answers, the asker decides what to do with the answers. GitHub and GitLab both answer "which repos exist?". ([0003](decisions/0003-extend-and-replace.md))
- **`Slot` (replace):** exactly one engine does the job: the repo store, where new secrets are written.

The kernel's `Contract` and `Slot` are the mechanism only. A language builds its own on top: untaped's call grammar subclasses `Contract` and checks its own rules ([0043](decisions/0043-contract-split.md)).

## Resolving a slot

In this order ([0029](decisions/0029-resolvers.md), [0039](decisions/0039-undecided.md), [0040](decisions/0040-scope-bindings-noscope.md)):

| Situation | Outcome |
|---|---|
| no scope is open | `NoScope` |
| the slot is bound in the current scope | the binding (whoever starts a run decides what's in it) |
| no engine fills it | `Unfilled(slot)` |
| one engine | that engine |
| several engines; every resolver is asked; exactly one distinct answer, naming an installed engine | that engine |
| several engines, no resolver answers | `Undecided(slot, engines)`: nothing chose |
| several engines, resolvers give different answers | `Ambiguous(slot, answers)`: resolvers disagree |

A binding may be any object implementing the slot (a test fake needs no entry point).

The kernel **never re-enters a slot it is resolving**. The inner request gets `Loop(chain)` as data; a resolver that hits it gives no answer, and only that resolver drops out ([0030](decisions/0030-loop-and-bootstrap.md)).

Extend providers have **no order** unless the resolvers agree on one ([0032](decisions/0032-picks-and-order-are-resolvers.md)). **A result never depends on install order.**

*Serves: the kernel never decides; an ambiguity is a valid state.*

## Calls

The kernel hands every call to one carrier, `Dispatch`, as a value ([0052](decisions/0052-call-shape.md)):

```python
Call(point, method, args, kwargs)            # the package
find(call) -> providers                      # the kernel's address book
invoke(call, provider) -> Result             # the kernel knocks on one door
Reply(provider: PluginId, result: Result)    # the receipt
Result = Ok(value) | Err(error)
```

- A dispatcher returns one `Reply` per provider found. One provider failing never sinks the rest ([0051](decisions/0051-reply-and-result.md)).
- **Nothing fills `Dispatch`:** the kernel composes its two tools minimally, one provider after another, in a fixed order (by identity) that keeps runs repeatable and means nothing:
  ```python
  [Reply(p, invoke(call, p)) for p in find(call)]
  ```
- **Slot calls:** `Ok` gives the value; `Err` is raised to the asker.
- `Dispatch` is chosen like any slot: a scope binding first, then resolvers ([0050](decisions/0050-dispatch-question-hook.md)). How calls are carried (cache, retry, timing, in what order) is the design of whoever fills it.
- The kernel calls its own hooks directly. They are never dispatched (dispatching them would loop through resolving `Dispatch`).
- `Reply.provider` is a `PluginId` (the distribution name), not a class.

*Serves: every operation ends in a known outcome; the kernel doesn't know where it runs.*

## Scope

A host opens a **scope** for each run (standard library `contextvars`). In it, slots can be bound to specific instances: one user's settings on a hub, a test fake ([0040](decisions/0040-scope-bindings-noscope.md)).

```python
with scope(settings_store=UserSettings(alice), capacity=Capacity(8)):
    ask(RepoSource).repos()
```

- `ask` outside any scope is refused with `NoScope`. A silent default scope would hand one user's state to another's job on a hub.
- Scopes side by side (a hub's jobs) are isolated by construction.
- Code that starts threads must carry the scope into them; a thread that doesn't sees `NoScope` ([0040](decisions/0040-scope-bindings-noscope.md)).

*Serves: the kernel doesn't know where it runs; the open scopes are part of its records.*

## Hooks

The kernel may offer a hook at its own limitation, so it can stay without opinions ([0049](decisions/0049-kernel-hooks-loaded.md)). A hook earns its place only when all three hold:

1. it sits at a point in the kernel's own job (finding and routing) where the kernel would otherwise need an opinion, or it holds knowledge only the kernel has;
2. the kernel still works with it empty;
3. no plugin could reach the same result by ordinary means.

A hook's shape is the one opinion nobody can swap, so it exposes a fact or a moment, never a policy.

| Kind | Rule | Hooks |
|---|---|---|
| **Moment** | the kernel announces something that happened; listeners only observe; all are called, in an order that means nothing; what one raises is reported as data | `Loaded` |
| **Question** | the kernel needs an answer it can't give without an opinion; each states how several answers combine and what happens when none comes | `Resolver`, `Dispatch` |

| Hook | Empty |
|---|---|
| `Loaded` | nobody checks |
| `Resolver` | several engines → `Undecided`; providers unordered |
| `Dispatch` | the kernel calls providers directly, one after another |

A "pre" hook that can stop or change what follows is a question in disguise, not a moment. `scope` is neither: the host passes it in.

## Facts

| Fact | Means |
|---|---|
| `LoadProblem` | a plugin failed to import; the others loaded |
| `Unfilled(slot)` | nothing fills the slot |
| `Undecided(slot, engines)` | several engines, and nothing chose: declare a preference |
| `Ambiguous(...)` | resolvers disagree, or two providers' answers overlap with no agreed order |
| `Loop(chain)` | the kernel was asked for a slot it is already resolving |
| `NoScope` | asked outside any scope |
| `Err(error)` | a provider's call failed; the error is the provider's, carried as is |

The kernel returns facts as data and never prints. The host decides how to show them.

## Pending

Rules set aside until multi-host is designed ([0056](decisions/0056-multi-host-parked.md)). They are not in force, and nothing above relies on them.

- Every shape that crosses `Dispatch` is plain data, rebuildable on a machine that has the contract's words but not necessarily its providers.
- An error that crosses machines arrives as itself when its class can be rebuilt on the receiving machine; otherwise it arrives in `Err` as a kernel fact holding its type name and message (placeholder name: `Opaque`).

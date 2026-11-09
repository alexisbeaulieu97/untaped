# Calls: the call grammar and `untaped-calls`

> Draft. Package names are placeholders. How `untaped-calls` orders its wrappers is not decided ([0045](../decisions/0045-two-kinds-of-order.md) is reopened).

Two first-party packages build on the kernel's calls. Neither is architecture: anyone can write their own.

## The call grammar (`untaped-call`)

untaped's language for contracts ([0031](../decisions/0031-call-grammar.md), [0043](../decisions/0043-contract-split.md)).

- Its `Contract` subclasses the kernel's and checks G1, G3, G5 and G6 for each contract (see [conventions](conventions.md#grammar-rank-2)).
- Every call is a **question** (`Question`: asks, safe to cache and retry) or an **action** (`Action`: changes something; gets confirmation and dry runs), like HTTP's GET and POST.
- It offers `CallWrapper`, the extension point for code that acts on every call.
- **Askers never name providers.** They combine an extend call's answers with the grammar's generic, pure `merge`, keyed by what the asker chooses ([0044](../decisions/0044-extend-answers-merge.md)):

  ```python
  # ✗ the asker knows who exists
  repos = by_provider[github] + by_provider[gitlab]

  # ✓ the asker states what makes two answers the same item
  repos = merge(ask(RepoSource).repos(), by=key(Repo).name)
  ```

  Items only one provider lists are kept. An overlap is settled by the resolvers' agreed order, or reported as `Ambiguous(item, providers)` for that item alone.
- The answers it merges are the kernel's `Reply`/`Result` ([0051](../decisions/0051-reply-and-result.md)); "who answered" serves the machinery and the user (merge order, `--columns +source`, failure messages), never the asker's logic.

## The dispatcher (`untaped-calls`)

Fills the kernel's `Dispatch` hook ([0050](../decisions/0050-dispatch-question-hook.md)). It carries every call: runs providers in parallel within the scope's `Capacity`, and applies the wrappers (cache, retry, timing, recording) around them.

It owns the order its wrappers run in. That design is open.

## Pending

Rules set aside until multi-host is designed ([0056](../decisions/0056-multi-host-parked.md)). They are not in force, and nothing above relies on them.

- A hub binds a different dispatcher per job, one that forwards calls to a runner.
- A forwarding dispatcher turns an error whose class can't be rebuilt on the receiving machine into the kernel's `Opaque` fact (placeholder name).
- The dispatcher owns how wrappers and forwarding combine.

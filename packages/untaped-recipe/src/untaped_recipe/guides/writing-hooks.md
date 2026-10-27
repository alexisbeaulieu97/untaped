---
description: "Read before writing a pack hook or using the built-in `yaml_edit` hook: the hook contract, how names resolve, verdicts and warnings."
---

## The contract

A hook module exports `transform()`, `validate()`, or both; the exported
name is the contract, and the pack's manifest rows declare only `module`.
`untaped recipe hooks init` scaffolds one, and `untaped recipe hooks run`
runs one against a fixture.

- A step's `args` reach the hook verbatim. Hooks read the resolved `inputs`
  natively (lists and dicts stay structured) and call
  `helpers.render_template()` for templated string args.
- Hooks stay pure while planning: they read only the target tree and their
  own pack, and never write or reach the network.
- A validate hook returns `helpers.pass_()`, `helpers.fail(msg)` or
  `helpers.skip(msg)`; skip means the recipe doesn't apply, and the target
  is `skipped`, not failed. Returning `None` passes; a plain string fails
  with that message.
- `helpers.warn(msg)` adds a warning to the target, any number of times,
  from validate and transform hooks; the hook still returns its verdict.

## How a hook name resolves

A bare hook name in a pack's recipe is the pack's own hook, else a
built-in, never another pack's; `pack/hook` names another pack's. Only
recipes outside a pack look bare names up across installed packs.

## The built-in `yaml_edit` hook

- `edits` is a list of `op: set|merge|delete|ensure`, each with a `path` of
  mapping keys, `{index: N}` or first-match `{where: {...}}` selectors.
- `ensure` adds a value only when absent: list membership by `match` keys
  or equality, or set-if-absent for mappings.
- String values render `{{ input }}` tokens and honour the args-level
  `unknown_tokens: keep`.
- A file whose content wouldn't change stays byte-identical.

`untaped docs recipe guides recipe-files` has a recipe that uses it.

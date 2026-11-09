# 0017. Stay in Python

- **Status:** accepted · settled before 2026-11-05 (7.6) · the maintainer
- **Rests on:** [0010](0010-one-process.md) one process; [0015](0015-builder-owns-the-list.md) the builder owns the list
- **Reopen if:** library-version conflicts between plugins stop being rare, or start-up can't be fixed by design (lazy imports). TypeScript would win if per-plugin private dependencies became essential. Compiled languages would still fail the "no rebuild" requirement.

## Problem

The maintainer was open to switching language for a cleaner architecture, but not "just to switch". Requirements used for the comparison:
1. install a plugin without rebuilding;
2. plugins share typed words in one process;
3. two plugins can use different versions of a library;
4. types readable at run time;
5. runs as data with cancellation;
6. a fast CLI on Windows;
7. domain libraries (comment-preserving YAML, Jinja, HTTP, TUI, MCP);
8. authors are DevOps people.

## Decision

Stay in Python.

## Rejected

- **Go, Rust, Zig, C/C++:** no practical in-process plugin loading without rebuilding. Go's `plugin` package doesn't work on Windows, Rust has no stable binary interface, and Zig is pre-1.0.
- **C#, Java/Kotlin:** they can load plugins in-process, but no builder exists for "add a plugin to an app", so untaped would own installs (the Poetry mistake, [0015](0015-builder-owns-the-list.md)). The JVM also starts slowly.
- **Gleam** (no interfaces or reflection; slow start) and **Lua** (untyped, thin standard library).
- **TypeScript, the runner-up.** It gives no install gain: npm -g and pnpm 11 isolate global installs per package, and oclif's answer is the CLI installing plugins itself. Beyond that:
  - half a gain on library versions, since words must be single-copy peer dependencies;
  - no start-up gain (bare node ≈ bare python ≈ 30 ms);
  - types are erased, so every word needs a runtime key;
  - a small gain on cancellation;
  - no real gain on front ends;
  - a worse supply-chain record.

## Consequences

Measured: `untaped --version` takes ~620 ms, about 450 ms of which is untaped importing its own modules eagerly. Bare python takes ~31 ms.

Three lessons kept:
1. Core's dependencies are everyone's dependencies, so core has none ([0018](0018-zero-dependency-core.md)).
2. Cancellation is a value the run carries.
3. Start-up is a design goal: lazy imports (PEP 810, accepted for 3.15, or the `__lazy_modules__` fallback on 3.14), plus importing only what the command needs.

WebAssembly components are a possible later bridge for non-Python plugins, not a foundation: they can't start programs, and Python components are heavy.

# Writing a plugin for the new untaped (draft)

For someone writing a plugin or a front end against the design in [book/](book/). It says what to do; the book says how untaped is, and its words ([book/words.md](book/words.md)) are used here as defined there. Labels such as (9c3) name the book entry a practice rests on.

This is a draft beside the prototypes. A practice is added only once a build or a decision by the maintainer has shown it; the build is named after each. It becomes `docs/reference/conventions.md` when the code moves. The exact lines a plugin reads and writes are in the latest prototype's `PROTOCOL.md` (`prototype/book4/`), not here.

## What becomes a plugin

Reuse goes through plugins, not through code that plugins share. Something several plugins need becomes a plugin they ask when it does any of these:

- keeps something between asks (a sign-in, a cache, a store of repositories);
- decides something that should be decided once for everyone (where files go, which secret store);
- touches the person or the world outside (the terminal, the network, the disk);
- should be replaceable.

Otherwise it is plain code that each plugin carries, such as turning a date into text. The one piece every plugin carries is the helper that talks to untaped.

Decided so far, each in the book: a plugin asks for the place to keep its files (g8n), for its token (w39) and for any secret (bwm). (`front`, `book5`)

## Choosing a kind for each contract

A folder gives each contract one kind (jw3).

| | per-ask | staying |
|---|---|---|
| What runs | a new program for each ask | one program for every ask |
| Cost of an ask | the program's start: about 1 ms native, 15 ms for bare Python | 55 to 90 µs |
| Can keep something between asks | no | yes |
| Can add contracts while it runs | no | yes (drf) |
| Smallest example | 6 lines of shell: `prototype/book4/examples/plugins/keychain/` | the sign-in holder, `examples/plugins/signin/` |

Choose staying when the plugin holds something (a sign-in, a cache, a lock) or is asked often. Otherwise per-ask is less to write and cannot leak anything between asks. (Measured in `kinds`, `book2`.)

## A staying plugin

- **Take a limited number of asks at a time and leave the rest untaken.** They wait (9vg). With 16 at a time, 1,000 slow asks at once were all answered in about 3.5 s, and a loop made by mistake stopped at 17 open asks. One thread for each ask, without a limit, took a machine down. The helper does this. (`book1`, `book2`)
- **Make each ask for the ask you are answering** (mdx). Inside a handler the helper does it. Naming another ask of your own, such as one you keep open for a long time, hides a loop from untaped, and your asks then end when that other ask does (9c3). (`rooted`, `cascade`)
- **You cannot start work that no ask caused** (9ng). To act on a timer, offer a contract the host asks and keeps open, and make your asks for that ask. A scheduler took 19 lines this way; a sign-in that renews itself, 20 more than without. (`rooted`)
- **Keep an ask about what is installed open**, and stop when your own plugin is gone (824, c26). The helper does this by default. (`book4`)

## When an ask ends

- **The asks you made for an ask end with it** (9c3). You do not have to leave them. (`cascade`)
- **Mark an ask to carry on when others also wait for its answer** (hnp). The sign-in holder marks its one question to the person, so the first asker giving up does not take it away from the other two. Then leave it yourself when the last one who waited is gone: nothing else will. (`cascade`, `book4`)
- **To clean up after your asker has left, make new asks for that ask before you leave it** (dzp). Say `done` last: an ask sent after it may be refused. (`rooted`, `book4`)
- **Before asking again after your answerer left, read whether the ask had been taken** (82r). If it had, something may be half done. A per-ask answerer always counts as having taken its ask. (`book4`)

## Contracts

- **Write a contract as types in your own language and have the file generated** (dwp). For jira, 642 lines of contract files became 312 lines of Python, with a list of failures written once. A file written by hand with a mistake in it matches nothing (5wb). (`front2`, `match2`)
- **List fields in the order that matters, the most telling first** (9v3). A front end shows as many as it has room for and takes required inputs as arguments in that order. Say nothing that holds for one kind of front end only (dzh): no short flags, no widths. (`front2`, `match`)
- **Your copy as an asker holds what your ask uses** (nc2); the library works out the fields you send. It cannot see which fields of the answer your code reads and assumes all of them, so say which you read when an older plugin refuses you for a field you do not need. (`match2`)
- **To change a contract without breaking older askers, add what is optional** (9wv): an optional input, an optional field in an answer, a new way to fail. Renaming or removing a field refuses the askers that use it (9v6). (`match2`)
- **Pass on why an ask of yours was refused.** untaped tells you which field the other side lacks; a person shown only "the keychain could not give the token" cannot act on it. (`match2`)
- **A plugin has one copy of each contract** (c24, r82), and a version of a plugin that must sit beside an older one is another folder (qfs). Two versions side by side need a choice for each contract both can answer. (`book4`)

## Helpers

- A helper program you start can use your way to reach untaped; its asks count as yours (3rh). When your program ends, untaped ends what it left running, and nothing reaches untaped by your way any more (22p, dc6). (`book3`, `book4`)

## A front end (a host)

- **Every piece of work starts with you** (9ng): only the host makes, for none, an ask that a plugin answers. While you answer an ask yourself, make your own asks for it, not for none, or you hide a loop through yourself. (`rooted`)
- **State a choice only where several installed plugins fill a contract** (4gh). A choice binds: when the plugin it names cannot answer, the asker is told and nothing else is tried (f55). What a person prefers, and which source wins, is yours to work out before you state it (ktt). (`book2`, `book3`)
- **Change a choice by stating another for the same contract** (t3d); there is then no moment without one. (`book4`)
- **You are told which plugin answers each ask** (32k), also when you ask every plugin. (`book3`)

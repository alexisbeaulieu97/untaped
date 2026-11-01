`untaped.sdk.init_project(path)` (experimental) makes a folder a project as
`untaped project init` does, recording the run's explicit profile pick, and
answers `Initialized` with the names it `picked`; `remove_project(project)`
removes one as `untaped project remove` does once it holds nothing. A
refused row from `Project.release` carries the owner's hint alone: the
plugin's command says how its user forces it.

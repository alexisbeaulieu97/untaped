A plugin marks a setting that locates data every project shares
`Annotated[Path | None, HomeOnly()]` (`untaped.sdk`, experimental), so a
project's `.untaped/config.yml` can't set it; `git.store_dir`,
`workspace.workspaces_dir`, `ansible.index_dir`, `recipe.library_dir` and
dotfiles' folders carry it.

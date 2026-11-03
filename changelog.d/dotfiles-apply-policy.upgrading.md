A timer or script that runs `untaped dotfiles apply --yes` treats exit 3 as
success (`SuccessExitStatus=3` under systemd): it means something waits for
a person, such as a modified file. To place every turned-on entry whatever
its policy, as `apply` did before, run `apply --all`. A script that reads
`status --check` gets 3 only when there is something for `apply` or a
person to do; a `once` file behind its source no longer counts. A package
whose `link` items should stay links sets `policy: sync`.

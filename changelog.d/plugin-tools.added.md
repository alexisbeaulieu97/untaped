`untaped plugin new NAME --fills OWNER.CONTRACT` scaffolds a plugin that
fills a contract, and `untaped plugin check [NAME]` checks an installed one:
its conventions, its owner's conformance checks, a live call of each filled
method and the contract schema it was tested against. `untaped.testing`
adds `compose_with`, `assert_fills` and `assert_contract_schemas`, and
doctor warns when a provider was tested against another schema than the
installed owner's (`owner-schema-drift`).
